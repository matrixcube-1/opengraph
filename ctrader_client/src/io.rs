//! Transport de trames : abstraction testable + implémentation TCP/TLS.
//!
//! `FrameIo` isole le codec de la couche réseau : les tests injectent
//! `MemIo` (duplex mémoire) à la place de `TlsIo`.

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio_rustls::client::TlsStream;

use crate::err::ClientError;
use crate::MAX_FRAME_SIZE;

/// Transport orienté trames cTrader : `send` écrit une trame complète,
/// `recv` attend la trame complète suivante (payload protobuf).
pub trait FrameIo {
    fn send<'a>(
        &'a mut self,
        bytes: &'a [u8],
    ) -> impl std::future::Future<Output = Result<(), ClientError>> + Send + 'a;
    fn recv<'a>(
        &'a mut self,
    ) -> impl std::future::Future<Output = Result<Vec<u8>, ClientError>> + Send + 'a;
    fn close<'a>(&'a mut self) -> impl std::future::Future<Output = ()> + Send + 'a;
}

// ---------------------------------------------------------------- TLS

/// Connexion TLS réelle : lecture/écriture séparées pour que l'envoi
/// (heartbeat, ordres) ne bloque jamais la réception des ticks.
pub struct TlsIo {
    rd: tokio::io::ReadHalf<TlsStream<TcpStream>>,
    wr: tokio::io::WriteHalf<TlsStream<TcpStream>>,
}

impl TlsIo {
    pub fn new(stream: TlsStream<TcpStream>) -> Self {
        let (rd, wr) = tokio::io::split(stream);
        Self { rd, wr }
    }
}

impl FrameIo for TlsIo {
    async fn send<'a>(&'a mut self, bytes: &'a [u8]) -> Result<(), ClientError> {
        self.wr.write_all(bytes).await?;
        self.wr.flush().await?;
        Ok(())
    }
    async fn recv<'a>(&'a mut self) -> Result<Vec<u8>, ClientError> {
        let mut len_buf = [0u8; 4];
        self.rd.read_exact(&mut len_buf).await?;
        let n = u32::from_be_bytes(len_buf) as usize;
        if n > MAX_FRAME_SIZE {
            return Err(ClientError::FrameTooLong(n));
        }
        if n == 0 {
            return Err(ClientError::EmptyFrame);
        }
        let mut buf = vec![0u8; n];
        self.rd.read_exact(&mut buf).await?;
        Ok(buf)
    }
    async fn close<'a>(&'a mut self) {
        let _ = self.wr.shutdown().await;
    }
}

// -------------------------------------------------------------- Mémoire

/// Duplex mémoire pour les tests : `MemIo` côté client, `MockPeer`
/// côté "serveur" (peut lire les trames envoyées et en injecter).
#[cfg(any(test, feature = "test_utils"))]
pub mod mock {
    use super::*;
    use tokio::sync::mpsc;

    /// Lit N octets depuis un buffer de réception (mini-parser de trames).
    fn take_frame(buf: &mut Vec<u8>) -> Option<Vec<u8>> {
        if buf.len() < 4 {
            return None;
        }
        let n = u32::from_be_bytes(buf[..4].try_into().unwrap()) as usize;
        if buf.len() < 4 + n {
            return None;
        }
        Some(buf.drain(..4 + n).skip(4).collect())
    }

    pub struct MemIo {
        tx: mpsc::UnboundedSender<Vec<u8>>,
        rx: mpsc::UnboundedReceiver<Vec<u8>>,
        rbuf: Vec<u8>,
    }

    pub struct MockPeer {
        pub rx: mpsc::UnboundedReceiver<Vec<u8>>,
        pub tx: mpsc::UnboundedSender<Vec<u8>>,
    }

    impl MockPeer {
        /// Trame brute complète (4B+payload) envoyée par le client.
        pub async fn recv_frame(&mut self) -> Option<Vec<u8>> {
            self.rx.recv().await
        }
        /// Injecte un payload protobuf (trame construite ici).
        pub fn send_payload(&self, payload: Vec<u8>) {
            let mut f = Vec::with_capacity(4 + payload.len());
            f.extend_from_slice(&(payload.len() as u32).to_be_bytes());
            f.extend_from_slice(&payload);
            let _ = self.tx.send(f);
        }
    }

    impl MemIo {
        pub fn pair() -> (MemIo, MockPeer) {
            let (c2s_tx, c2s_rx) = mpsc::unbounded_channel();
            let (s2c_tx, s2c_rx) = mpsc::unbounded_channel();
            (
                MemIo {
                    tx: c2s_tx,
                    rx: s2c_rx,
                    rbuf: Vec::new(),
                },
                MockPeer {
                    rx: c2s_rx,
                    tx: s2c_tx,
                },
            )
        }
    }

    impl FrameIo for MemIo {
        async fn send<'a>(&'a mut self, bytes: &'a [u8]) -> Result<(), ClientError> {
            self.tx
                .send(bytes.to_vec())
                .map_err(|_| ClientError::Dead("mock tx fermé".into()))
        }
        async fn recv<'a>(&'a mut self) -> Result<Vec<u8>, ClientError> {
            loop {
                if let Some(p) = take_frame(&mut self.rbuf) {
                    return Ok(p);
                }
                let chunk = self
                    .rx
                    .recv()
                    .await
                    .ok_or_else(|| ClientError::Dead("mock rx fermé".into()))?;
                self.rbuf.extend_from_slice(&chunk);
            }
        }
        async fn close<'a>(&'a mut self) {
            let _ = self.tx.send(Vec::new());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::mock::*;
    use super::*;

    #[tokio::test]
    async fn memio_delivers_full_frames() {
        let (mut cli, mut srv) = MemIo::pair();
        // le client envoie une trame ; le serveur la lit entièrement
        let body = b"hello-proto".to_vec();
        let mut fr = Vec::new();
        fr.extend_from_slice(&(body.len() as u32).to_be_bytes());
        fr.extend_from_slice(&body);
        srv.send_payload(body.clone());
        let got = cli.recv().await.unwrap();
        assert_eq!(got, body);
        // le serveur voit la trame côté client->serveur
        cli.send(&fr).await.unwrap();
        let raw = srv.recv_frame().await.unwrap();
        assert_eq!(raw[4..], fr[4..]);
    }

    #[tokio::test]
    async fn memio_reassembles_split_frames() {
        let (mut cli, srv) = MemIo::pair();
        // deux trames collées + une trame coupée en deux chunks
        let mk = |b: &[u8]| {
            let mut f = (b.len() as u32).to_be_bytes().to_vec();
            f.extend_from_slice(b);
            f
        };
        let a = mk(b"AAAA");
        let b = mk(b"BB");
        let c = mk(b"CCCCCC");
        let mut all = a.clone();
        all.extend_from_slice(&b[..3]); // trame b coupée
        let _ = srv.tx.send(all);
        let _ = srv.tx.send(b[3..].to_vec());
        let _ = srv.tx.send(c);
        assert_eq!(cli.recv().await.unwrap(), b"AAAA");
        assert_eq!(cli.recv().await.unwrap(), b"BB");
        assert_eq!(cli.recv().await.unwrap(), b"CCCCCC");
    }
}
