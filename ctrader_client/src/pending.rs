//! Corrélation requête/réponse par `clientMsgId` (enveloppe ProtoMessage)
//! et par `clientOrderId` (événements d'exécution).

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tokio::sync::oneshot;

use crate::err::ClientError;
use crate::proto::spotware::ProtoMessage;

/// File des requêtes en attente de réponse (`clientMsgId` -> oneshot).
#[derive(Clone, Default)]
pub struct Pending {
    map: Arc<Mutex<HashMap<String, oneshot::Sender<ProtoMessage<'static>>>>>,
}

impl Pending {
    /// Enregistre un `clientMsgId` avant l'envoi de la requête.
    /// Retourne le receveur à attendre (timeout par l'appelant).
    pub fn register(&self, cmid: &str) -> oneshot::Receiver<ProtoMessage<'static>> {
        let (tx, rx) = oneshot::channel();
        self.map.lock().unwrap().insert(cmid.to_string(), tx);
        rx
    }

    /// `cmid` en attente de réponse ? (utilisé par le dispatcher avant
    /// de consommer le message via `resolve`).
    pub fn is_pending(&self, cmid: &str) -> bool {
        self.map.lock().unwrap().contains_key(cmid)
    }

    /// Résout la requête si le message porte un `clientMsgId` attendu.
    /// Retourne true si une requête en attente a été résolue.
    pub fn resolve(&self, msg: ProtoMessage<'static>) -> bool {
        if let Some(cmid) = msg.clientMsgId.as_deref() {
            if let Some(tx) = self.map.lock().unwrap().remove(cmid) {
                let _ = tx.send(msg);
                return true;
            }
        }
        false
    }

    /// Réponse + timeout : nettoie la table si le timeout expire.
    pub async fn wait(
        &self,
        cmid: &str,
        rx: oneshot::Receiver<ProtoMessage<'static>>,
        timeout: Duration,
        what: &'static str,
    ) -> Result<ProtoMessage<'static>, ClientError> {
        match tokio::time::timeout(timeout, rx).await {
            Ok(Ok(m)) => Ok(m),
            Ok(Err(_)) => Err(ClientError::Dead(format!("canal {what} fermé"))),
            Err(_) => {
                self.map.lock().unwrap().remove(cmid);
                Err(ClientError::Timeout(what))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::borrow::Cow;

    fn env(cmid: &str) -> ProtoMessage<'static> {
        ProtoMessage {
            payloadType: 2101,
            payload: Some(Cow::Owned(vec![])),
            clientMsgId: Some(Cow::Owned(cmid.to_string())),
        }
    }

    #[tokio::test]
    async fn register_resolve_roundtrip() {
        let p = Pending::default();
        let rx = p.register("c1");
        assert!(p.resolve(env("c1")));
        let m = p
            .wait("c1", rx, Duration::from_secs(1), "auth")
            .await
            .unwrap();
        assert_eq!(m.payloadType, 2101);
    }

    #[tokio::test]
    async fn resolve_ignores_unknown_cmid() {
        let p = Pending::default();
        assert!(!p.resolve(env("inconnu")));
        // message sans clientMsgId -> pas résolu
        let m = ProtoMessage {
            payloadType: 51,
            payload: None,
            clientMsgId: None,
        };
        assert!(!p.resolve(m));
    }

    #[tokio::test]
    async fn wait_timeout_cleans_table() {
        let p = Pending::default();
        let rx = p.register("c2");
        let r = p
            .wait("c2", rx, Duration::from_millis(10), "symbols")
            .await;
        assert!(matches!(r, Err(ClientError::Timeout("symbols"))));
        // un message tardif ne résout plus rien
        assert!(!p.resolve(env("c2")));
    }
}
