//! Économie d'exécution (IC Markets cTrader).

/// Notionnel par transaction, en USD (top-of-book POC).
pub const NOTIONAL_USD: f64 = 100_000.0;

/// Commission par côté et par 100 K$ — taux négocié (6 $ aller-retour).
/// Convention canonique du document : 3,50 — recalibrable via `comm_scale >= 1.0`.
pub const COMM_PER_SIDE_USD: f64 = 3.00;

/// Repli du coût d'abort par jambe remplie — utilisé uniquement quand la
/// médiane de spread de l'arête est absente/non finie (audit OG2-D2 : le
/// coût réel = `med_rel x NOTIONAL + COMM`, ~23 $ sur NZDJPY vs 12 $ ici).
pub const ABORT_COST_PER_LEG_USD: f64 = 12.0;
