//! Instantané immuable de régime — conception §6.
use std::sync::atomic::{AtomicU64, Ordering};

/// Instantané publié par la boucle lente, lu sans verrou par le chemin chaud.
#[derive(Debug, Clone)]
pub struct RegimeSnapshot {
    /// Numéro de séquence strictement croissant (journalisé par décision).
    pub seq: u64,
    /// Horodatage événementiel de l'instantané (ns).
    pub event_time_ns: u64,
    /// État de la porte à hystérésis.
    pub gate_open: bool,
    /// Tension standardisée T̃ (z robuste), None si indisponible.
    pub t_tilde: Option<f64>,
    /// Dominance spectrale D ∈ [1/7, 1], None si indisponible.
    pub dominance: Option<f64>,
    /// Devise dominante c⋆ (indice 0..7), None si ∅.
    pub dominant: Option<usize>,
    /// Direction du facteur dominant (+1/−1).
    pub direction: i8,
}

/// Éditeur : publie un instantané par échange atomique de pointeur.
pub struct RegimePublisher {
    slot: arc_swap::ArcSwap<RegimeSnapshot>,
    seq: AtomicU64,
}

impl RegimePublisher {
    pub fn new(snap: RegimeSnapshot) -> Self {
        Self {
            slot: arc_swap::ArcSwap::from_pointee(snap),
            seq: AtomicU64::new(0),
        }
    }

    /// Publie : écrase le seq par le compteur interne +1 (strict. croissant).
    pub fn publish(&self, mut snap: RegimeSnapshot) -> u64 {
        let seq = self.seq.fetch_add(1, Ordering::SeqCst) + 1;
        snap.seq = seq;
        self.slot.store(std::sync::Arc::new(snap));
        seq
    }

    /// Lecture sans verrou (chemin chaud).
    pub fn load(&self) -> arc_swap::Guard<std::sync::Arc<RegimeSnapshot>> {
        self.slot.load()
    }
}

/// Chemin chaud : une lecture, un test de fraîcheur, un test de bit (§7.3).
#[inline]
pub fn regime_allows_send(s: &RegimeSnapshot, now_ns: u64, stale_ns: u64) -> bool {
    s.gate_open && now_ns >= s.event_time_ns && now_ns - s.event_time_ns <= stale_ns
}
