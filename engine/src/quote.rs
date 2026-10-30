//! Tick de cotation (Dukascopy, résolution ms).

/// Une mise à jour top-of-book.
///
/// `vol` est le volume affiché en unités propres à l'instrument
/// (millions de devise de base pour FX d'après la convention du flux) —
/// la sémantique par classe CFD reste à spécifier avant activation.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Quote {
    /// Horodatage en millisecondes epoch.
    pub ts_ms: i64,
    pub bid: f64,
    pub ask: f64,
    pub vol: f64,
}

impl Quote {
    pub fn mid(&self) -> f64 {
        0.5 * (self.bid + self.ask)
    }
}
