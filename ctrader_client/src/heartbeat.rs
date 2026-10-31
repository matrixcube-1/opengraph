//! Heartbeat émetteur (10 s) + watchdog (35 s) + backoff de reconnexion.
//!
//! Protocole cTrader : heartbeat transport = `ProtoMessage` payloadType 51.
//! Le serveur envoie ~1 heartbeat/10 s ; le client DOIT émettre aussi —
//! l'inactivité client ~30 s provoque une déconnexion venue.

use std::borrow::Cow;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use rand::Rng;

use crate::proto::spotware::{ProtoHeartbeatEvent, ProtoMessage, ProtoPayloadType};

/// Cadences (constantes du guide, confirmées doc officielle).
pub const HB_SEND: Duration = Duration::from_secs(10);
/// Sans trafic venue pendant 35 s -> connexion considérée morte.
pub const HB_WATCHDOG: Duration = Duration::from_secs(35);
pub const BACKOFF_MIN: Duration = Duration::from_secs(1);
pub const BACKOFF_MAX: Duration = Duration::from_secs(30);

/// Horloge de dernière activité venue partagée entre reader et watcher.
/// `None` = connexion vient de s'ouvrir (le watchdog tolère ce cas).
#[derive(Clone, Default)]
pub struct LastRx(Arc<Mutex<Option<Instant>>>);

impl LastRx {
    /// Marque une réception (appelé pour chaque frame venue).
    pub fn touch(&self) {
        *self.0.lock().unwrap() = Some(Instant::now());
    }
    /// Âge depuis la dernière réception ; `None` si jamais reçu.
    pub fn age(&self) -> Option<Duration> {
        self.0.lock().unwrap().map(|i| i.elapsed())
    }
}

/// Enveloppe heartbeat transport (payloadType 51, corps vide — le
/// timestamp est optionnel dans `ProtoHeartbeatEvent`).
pub fn heartbeat_msg() -> ProtoMessage<'static> {
    ProtoMessage {
        payloadType: ProtoPayloadType::HEARTBEAT_EVENT as u32,
        payload: Some(Cow::Owned(
            crate::net::encode_payload(&ProtoHeartbeatEvent::default()).unwrap_or_default(),
        )),
        clientMsgId: None,
    }
}

/// Backoff exponentiel + jitter (×0.75..1.25), borné à 30 s.
pub struct Backoff {
    attempt: u32,
}

impl Backoff {
    pub fn new() -> Self {
        Self { attempt: 0 }
    }
    /// Délai avant la prochaine tentative de reconnexion.
    pub fn next_delay(&mut self) -> Duration {
        self.attempt += 1;
        let exp = 1u32 << self.attempt.saturating_sub(1).min(5);
        let cap = BACKOFF_MIN.saturating_mul(exp).min(BACKOFF_MAX);
        cap.mul_f64(rand::thread_rng().gen_range(0.75..=1.25))
    }
    pub fn reset(&mut self) {
        self.attempt = 0;
    }
    pub fn attempt(&self) -> u32 {
        self.attempt
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn heartbeat_msg_payload_type_51() {
        assert_eq!(heartbeat_msg().payloadType, 51);
    }

    #[test]
    fn fresh_connection_not_dead() {
        let rx = LastRx::default();
        assert!(rx.age().is_none(), "jamais reçu -> pas encore mort");
        rx.touch();
        assert!(rx.age().unwrap() < Duration::from_secs(1));
    }

    #[test]
    fn backoff_exponential_then_capped() {
        let mut b = Backoff::new();
        let d0 = b.next_delay();
        assert!(d0 >= Duration::from_millis(750) && d0 <= Duration::from_millis(1_250));
        let mut last = Duration::ZERO;
        for _ in 0..10 {
            last = b.next_delay();
        }
        assert!(last <= Duration::from_secs(38), "borné : {last:?}");
        assert!(b.attempt() == 11);
        b.reset();
        assert_eq!(b.attempt(), 0);
    }
}
