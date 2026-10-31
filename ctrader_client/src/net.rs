//! Pompe réseau : une tâche par connexion qui multiplexe envois
//! (ordres, requêtes, heartbeats) et réceptions (frames venue) sur
//! `FrameIo`, plus la corrélation `clientMsgId` des requêtes.
//!
//! ```text
//! [venue] --frame--> pump --Incoming::Msg--> strategy
//! [venue] <--frame-- pump <--Outbound------ strategy
//! requêtes : send_request() -> Pending -> oneshot -> résolue par le
//!            dispatcher quand la réponse porte le même clientMsgId
//! ```

use std::borrow::Cow;
use std::time::Duration;

use tokio::net::TcpStream;
use tokio::sync::{mpsc, oneshot};
use tokio_rustls::rustls::{ClientConfig, RootCertStore};
use tokio_rustls::TlsConnector;

use crate::codec::{decode_msg, frame};
use crate::config::Env;
use crate::err::ClientError;
use crate::heartbeat::LastRx;
use crate::io::{FrameIo, TlsIo};
use crate::pending::Pending;
use crate::proto::spotware::ProtoMessage;

/// Événement entrant pour la stratégie.
#[derive(Debug)]
pub enum Incoming {
    /// Enveloppe venue décodée (heartbeat, spot, execution, réponse, ...).
    Msg(ProtoMessage<'static>),
    /// Le transport est tombé (EOF/erreur/heartbeat mort) -> reconnect.
    Dead(String),
}

/// Canal d'envoi : la stratégie pousse des enveloppes complètes.
#[derive(Clone)]
pub struct Outbound {
    tx: mpsc::Sender<ProtoMessage<'static>>,
}

impl Outbound {
    /// Envoi fire-and-forget (ordres, souscriptions, heartbeats).
    pub async fn send(&self, msg: ProtoMessage<'static>) -> Result<(), ClientError> {
        self.tx
            .send(msg)
            .await
            .map_err(|_| ClientError::Dead("outbound fermé".into()))
    }
}

/// Boucle réseau en cours.
pub struct NetHandle {
    pub outbound: Outbound,
    pub inbound: mpsc::Receiver<Incoming>,
    pub pending: Pending,
    /// Horloge partagée de réception (watchdog).
    pub last_rx: LastRx,
}

/// Connecte TCP+TLS (rustls, TCP_NODELAY) vers `env` — port 5035.
pub async fn connect(env: Env, timeout: Duration) -> Result<TlsIo, ClientError> {
    let addr = format!("{}:{}", env.host(), env.port());
    let tcp = tokio::time::timeout(timeout, TcpStream::connect(&addr))
        .await
        .map_err(|_| ClientError::Timeout("tcp connect"))?
        .map_err(ClientError::from)?;
    tcp.set_nodelay(true).ok();

    let mut roots = RootCertStore::empty();
    roots.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    let cfg = ClientConfig::builder()
        .with_root_certificates(roots)
        .with_no_client_auth();
    let connector = TlsConnector::from(std::sync::Arc::new(cfg));
    let name = tokio_rustls::rustls::pki_types::ServerName::try_from(env.host())
        .map_err(|e| ClientError::Inconsistent(format!("servername: {e}")))?;
    let tls = tokio::time::timeout(timeout, connector.connect(name, tcp))
        .await
        .map_err(|_| ClientError::Timeout("tls handshake"))?
        .map_err(ClientError::from)?;
    Ok(TlsIo::new(tls))
}

/// Lance la pompe sur un `FrameIo` quelconque (TLS réel ou `MemIo` mock).
/// `last_rx` est touché à chaque frame venue — alimente le watchdog.
pub fn spawn<Io>(mut io: Io, last_rx: LastRx) -> NetHandle
where
    Io: FrameIo + Send + 'static,
{
    let (out_tx, mut out_rx) = mpsc::channel::<ProtoMessage<'static>>(256);
    let (in_tx, in_rx) = mpsc::channel::<Incoming>(512);
    let pump_rx = last_rx.clone();

    tokio::spawn(async move {
        loop {
            tokio::select! {
                biased;
                // priorité aux envois : un ordre ne doit jamais attendre
                // derrière un flot de ticks.
                msg = out_rx.recv() => {
                    let Some(msg) = msg else { return };
                    match frame(&msg) {
                        Ok(bytes) => {
                            if let Err(e) = io.send(&bytes).await {
                                let _ = in_tx.send(Incoming::Dead(format!("send: {e}"))).await;
                                return;
                            }
                        }
                        Err(e) => tracing::error!(error = %e, "encodage trame impossible"),
                    }
                }
                r = io.recv() => {
                    match r {
                        Ok(payload) => {
                            pump_rx.touch();
                            match decode_msg(&payload) {
                                Ok(m) => {
                                    if in_tx.send(Incoming::Msg(m)).await.is_err() {
                                        return;
                                    }
                                }
                                Err(e) => tracing::warn!(error = %e, "frame indécodable"),
                            }
                        }
                        Err(e) => {
                            let _ = in_tx.send(Incoming::Dead(format!("recv: {e}"))).await;
                            return;
                        }
                    }
                }
            }
        }
    });

    NetHandle {
        outbound: Outbound { tx: out_tx },
        inbound: in_rx,
        pending: Pending::default(),
        last_rx,
    }
}

/// Construit une enveloppe requête : payload encodé + `clientMsgId` unique,
/// pending enregistré AVANT l'envoi (jamais de course).
/// Retourne `(clientMsgId, receveur)` — le cmid sert à `await_response`.
pub async fn send_request<M: quacky::MessageWrite>(
    handle: &NetHandle,
    payload_type: crate::proto::spotware::ProtoOAPayloadType,
    msg: &M,
) -> Result<(String, oneshot::Receiver<ProtoMessage<'static>>), ClientError> {
    let cmid = uuid::Uuid::new_v4().to_string();
    let rx = handle.pending.register(&cmid);
    let env = crate::codec::envelope(payload_type, encode_payload(msg)?, Some(cmid.as_str()));
    let env: ProtoMessage<'static> = ProtoMessage {
        payloadType: env.payloadType,
        payload: env.payload.map(|c| Cow::Owned(c.into_owned())),
        clientMsgId: env.clientMsgId.map(|c| Cow::Owned(c.into_owned())),
    };
    handle.outbound.send(env).await?;
    Ok((cmid, rx))
}

/// Attend la réponse corrélée ; nettoie le pending si timeout.
pub async fn await_response(
    pending: &Pending,
    cmid: &str,
    rx: oneshot::Receiver<ProtoMessage<'static>>,
    timeout: Duration,
    what: &'static str,
) -> Result<ProtoMessage<'static>, ClientError> {
    pending.wait(cmid, rx, timeout, what).await
}

/// Sérialise un message métier en payload protobuf.
pub fn encode_payload<M: quacky::MessageWrite>(msg: &M) -> Result<Vec<u8>, ClientError> {
    let mut buf = vec![0u8; msg.get_size()];
    let mut w = quacky::Writer::new(quacky::BytesWriter::new(&mut buf));
    msg.write_message(&mut w)
        .map_err(|e| ClientError::Proto(e.to_string()))?;
    Ok(buf)
}

/// Décode le payload d'une enveloppe en `M`.
pub fn decode_payload<'a, M: quacky::MessageRead<'a>>(
    msg: &'a ProtoMessage<'a>,
) -> Result<M, ClientError> {
    let p = msg
        .payload
        .as_ref()
        .ok_or_else(|| ClientError::Inconsistent("enveloppe sans payload".into()))?;
    let mut r = quacky::BytesReader::from_bytes(p);
    M::from_reader(&mut r, p).map_err(|e| ClientError::Proto(e.to_string()))
}

/// Route une enveloppe : résout le pending si `clientMsgId` attendu ;
/// retourne true si le message a été consommé par une requête.
pub fn dispatch_response(handle: &NetHandle, msg: ProtoMessage<'static>) -> bool {
    if let Some(cmid) = msg.clientMsgId.as_deref() {
        if handle.pending.is_pending(cmid) {
            return handle.pending.resolve(msg);
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::codec::{decode_msg, frame};
    use crate::io::mock::*;
    use crate::proto::spotware::{
        ProtoOAAccountAuthReq, ProtoOAAccountAuthRes, ProtoOAPayloadType, ProtoOASpotEvent,
    };

    /// Serveur mock : attend une trame auth, répond 2103 avec le même
    /// clientMsgId, puis pousse un SpotEvent non sollicité.
    async fn mock_flow(peer: &mut MockPeer) {
        let raw = peer.recv_frame().await.unwrap();
        let req = decode_msg(&raw[4..]).unwrap();
        assert_eq!(req.payloadType, ProtoOAPayloadType::PROTO_OA_ACCOUNT_AUTH_REQ as u32);
        let cmid = req.clientMsgId.clone().unwrap().into_owned();
        // réponse account auth
        let res = ProtoOAAccountAuthRes {
            payloadType: Some(ProtoOAPayloadType::PROTO_OA_ACCOUNT_AUTH_RES),
            ctidTraderAccountId: 42,
        };
        let env = crate::codec::envelope(
            ProtoOAPayloadType::PROTO_OA_ACCOUNT_AUTH_RES,
            encode_payload(&res).unwrap(),
            Some(&cmid),
        );
        peer.send_payload(frame(&env).unwrap());
        // événement non sollicité
        let spot = ProtoOASpotEvent {
            payloadType: Some(ProtoOAPayloadType::PROTO_OA_SPOT_EVENT),
            ctidTraderAccountId: 42,
            symbolId: 7,
            bid: Some(150_123),
            ask: Some(150_130),
            ..Default::default()
        };
        let env = crate::codec::envelope(
            ProtoOAPayloadType::PROTO_OA_SPOT_EVENT,
            encode_payload(&spot).unwrap(),
            None,
        );
        peer.send_payload(frame(&env).unwrap());
    }

    #[tokio::test]
    async fn request_response_and_event_routing() {
        let (io, mut peer) = MemIo::pair();
        let mut handle = spawn(io, LastRx::default());

        // la requête doit partir — le mock répond + pousse un spot
        let req = ProtoOAAccountAuthReq {
            payloadType: Some(ProtoOAPayloadType::PROTO_OA_ACCOUNT_AUTH_REQ),
            accessToken: Cow::Borrowed("tok-x"),
            ctidTraderAccountId: 42,
        };
        let (_cmid, rx) =
            send_request(&handle, ProtoOAPayloadType::PROTO_OA_ACCOUNT_AUTH_REQ, &req)
                .await
                .unwrap();
        let _ = rx;
        mock_flow(&mut peer).await;

        // dispatcher : la réponse résout le pending, le spot remonte
        let mut got_spot = false;
        let mut got_resp = false;
        tokio::time::timeout(Duration::from_secs(2), async {
            while !(got_spot && got_resp) {
                match handle.inbound.recv().await {
                    Some(Incoming::Msg(m)) => {
                        if dispatch_response(&handle, m.clone()) {
                            got_resp = true;
                        } else {
                            assert_eq!(m.payloadType, 2131);
                            got_spot = true;
                        }
                    }
                    other => panic!("inbound inattendu: {other:?}"),
                }
            }
        })
        .await
        .expect("flux dans le délai");
        assert!(got_spot && got_resp);
    }

    #[tokio::test]
    async fn dead_transport_signals_reconnect() {
        let (io, peer) = MemIo::pair();
        let mut handle = spawn(io, LastRx::default());
        drop(peer); // le serveur disparaît -> recv doit mourir
        match tokio::time::timeout(Duration::from_secs(2), handle.inbound.recv()).await {
            Ok(Some(Incoming::Dead(_))) => {}
            other => panic!("attendu Dead, eu {other:?}"),
        }
    }
}
