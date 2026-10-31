//! Transport de trames : moitiés lecture/écriture séparées.
//!
//! `read_exact` n'est PAS cancel-safe : un `recv()` annulé par `select!`
//! perd les octets déjà consommés et désynchronise le flux. Le transport
//! est donc **séparable** : la pompe réseau utilise une tâche lecteur
//! (`FrameRd` -> `recv()` jamais annulé) et une tâche écrivain (`FrameWr`)
//! indépendantes — pas de `select!` autour de la lecture.

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio_rustls::client::TlsStream;

use crate::err::ClientError;
use crate::MAX_FRAME_SIZE;

/// Moitié lecture : attend la trame complète suivante (payload protobuf).
/// Une seule tâche la pilote — `recv` ne doit jamais être annulé.
pub trait FrameRd {
    fn recv<'a>(
        &'a mut self,
    ) -> impl std::future::Future<Output = Result<Vec<u8>, ClientError>> + Send + 'a;
}

/// Moitié écriture : écrit une trame complète (4B-BE + payload).
pub trait FrameWr {
    fn send<'a>(
        &'a mut self,
        bytes: &'a [u8],
    ) -> impl std::future::Future<Output = Result<(), ClientError>> + Send + 'a;
}

/// Transport duplex séparable en ses deux moitiés.
pub trait FrameIo {
    type Rd: FrameRd;
    type Wr: FrameWr;
    fn split(self) -> (Self::Rd, Self::Wr);
}

// ---------------------------------------------------------------- TLS

/// Lecture TLS (moitié de `TlsStream<TcpStream>`).
pub struct TlsRd {
    rd: tokio::io::ReadHalf<TlsStream<TcpStream>>,
}
/// Écriture TLS.
pub struct TlsWr {
    wr: tokio::io::WriteHalf<TlsStream<TcpStream>>,
}

/// Connexion TLS réelle — `split` consomme et rend les moitiés.
pub struct TlsIo {
    rd: TlsRd,
    wr: TlsWr,
}

impl TlsIo {
    pub fn new(stream: TlsStream<TcpStream>) -> Self {
        let (rd, wr) = tokio::io::split(stream);
        Self {
            rd: TlsRd { rd },
            wr: TlsWr { wr },
        }
    }
}

impl FrameIo for TlsIo {
    type Rd = TlsRd;
    type Wr = TlsWr;
    fn split(self) -> (TlsRd, TlsWr) {
        (self.rd, self.wr)
    }
}

impl FrameRd for TlsRd {
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
}

impl FrameWr for TlsWr {
    async fn send<'a>(&'a mut self, bytes: &'a [u8]) -> Result<(), ClientError> {
        self.wr.write_all(bytes).await?;
        self.wr.flush().await?;
        Ok(())
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

    pub struct MemRd {
        rx: mpsc::UnboundedReceiver<Vec<u8>>,
        rbuf: Vec<u8>,
    }
    pub struct MemWr {
        tx: mpsc::UnboundedSender<Vec<u8>>,
    }
    pub struct MemIo {
        rd: MemRd,
        wr: MemWr,
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
                    rd: MemRd {
                        rx: s2c_rx,
                        rbuf: Vec::new(),
                    },
                    wr: MemWr { tx: c2s_tx },
                },
                MockPeer {
                    rx: c2s_rx,
                    tx: s2c_tx,
                },
            )
        }
    }

    impl FrameIo for MemIo {
        type Rd = MemRd;
        type Wr = MemWr;
        fn split(self) -> (MemRd, MemWr) {
            (self.rd, self.wr)
        }
    }

    impl FrameRd for MemRd {
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
    }

    impl FrameWr for MemWr {
        async fn send<'a>(&'a mut self, bytes: &'a [u8]) -> Result<(), ClientError> {
            self.tx
                .send(bytes.to_vec())
                .map_err(|_| ClientError::Dead("mock tx fermé".into()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::mock::*;
    use super::*;

    #[tokio::test]
    async fn memio_delivers_full_frames() {
        let (io, mut srv) = MemIo::pair();
        let (mut cli_rd, mut cli_wr) = io.split();
        // le client envoie une trame ; le serveur la lit entièrement
        let body = b"hello-proto".to_vec();
        let mut fr = Vec::new();
        fr.extend_from_slice(&(body.len() as u32).to_be_bytes());
        fr.extend_from_slice(&body);
        srv.send_payload(body.clone());
        let got = cli_rd.recv().await.unwrap();
        assert_eq!(got, body);
        // le serveur voit la trame côté client->serveur
        cli_wr.send(&fr).await.unwrap();
        let raw = srv.recv_frame().await.unwrap();
        assert_eq!(raw[4..], fr[4..]);
    }

    #[tokio::test]
    async fn memio_reassembles_split_frames() {
        let (io, srv) = MemIo::pair();
        let (mut cli_rd, _cli_wr) = io.split();
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
        assert_eq!(cli_rd.recv().await.unwrap(), b"AAAA");
        assert_eq!(cli_rd.recv().await.unwrap(), b"BB");
        assert_eq!(cli_rd.recv().await.unwrap(), b"CCCCCC");
    }

    /// Régression C1 : une réception interrompue par un envoi ne doit pas
    /// perdre d'octets — la lecture vit dans sa propre tâche, non annulable.
    #[tokio::test]
    async fn send_during_partial_recv_loses_nothing() {
        let (io, mut srv) = MemIo::pair();
        let (mut rd, mut wr) = io.split();
        // la venue pousse une trame en deux morceaux distants
        let body = b"PARTIAL-FRAME-BODY".to_vec();
        let mut f = (body.len() as u32).to_be_bytes().to_vec();
        f.extend_from_slice(&body);
        let (h, t) = f.split_at(6);
        let _ = srv.tx.send(h.to_vec());
        // envoi concurrent pendant que la moitié seulement est arrivée
        let mut out = Vec::new();
        out.extend_from_slice(&3u32.to_be_bytes());
        out.extend_from_slice(b"out");
        wr.send(&out).await.unwrap();
        let _ = srv.tx.send(t.to_vec());
        // la trame doit arriver intacte malgré l'envoi entrelacé
        assert_eq!(rd.recv().await.unwrap(), body);
        let sent = srv.recv_frame().await.unwrap();
        assert_eq!(&sent[4..], b"out");
    }
}
