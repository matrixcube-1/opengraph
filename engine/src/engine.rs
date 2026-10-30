//! État de marché forward-fill — à implémenter (runbook étape 3).
//!
//! Référence comportementale : `poc_engine.Engine` (R7).
//!
//! ## Sémantique requise
//!
//! - Chaque symbole garde un curseur `pos` **rembobinable** : `sync(t)` et
//!   `tick(s, t)` ne publient une quote que si `ts[pos] <= t` ; aucun état ne
//!   peut exposer le premier tick futur (bug R7 corrigé — régression T1-T6).
//! - `quote_at(s, t)` est *sans état* : dernier tick `<= t` via dichotomie
//!   (`searchsorted(ts, t, right) - 1`), `-1` si aucun.
//! - `usd_price(node)` = prix USD de chaque devise au mid courant des paires
//!   USD — utilisé pour conversions de notional et marquage résiduel mid FX.
//! - Le moteur ne contient que l'univers FX sous Convention A.

use crate::quote::Quote;

/// Série de ticks ordonnée d'un symbole (ts croissant strict).
pub struct SymbolSeries {
    pub sym: &'static str,
    pub quotes: Vec<Quote>,
}

/// État de marché : séries + curseurs rembobinables + quotes courantes.
pub struct Engine {
    // séries: Vec<SymbolSeries>,
    // pos: Vec<usize>,          // curseur par symbole — rembobinable
    // cur: Vec<Option<Quote>>,  // dernière quote <= t (None = jamais vue)
    // last_t: Vec<i64>,
}

impl Engine {
    /// Dernier tick `<= t` — sans effet de bord (dichotomie).
    pub fn quote_at(&self, _sym: usize, _t: i64) -> Option<(i64, Quote)> {
        unimplemented!("runbook etape 3 — port de quote_at")
    }

    /// Publie l'état à l'instant t (forward-fill, jamais de futur).
    pub fn sync(&mut self, _t: i64) {
        unimplemented!("runbook etape 3 — port de sync/_advance rembobinable")
    }

    /// Prix USD courant de chaque nœud devise (mid), NaN si indisponible.
    pub fn usd_price(&self) -> [f64; 8] {
        unimplemented!("runbook etape 3 — port de usd_price")
    }
}
