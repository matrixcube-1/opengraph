//! Économie d'exécution (IC Markets cTrader).

/// Notionnel par transaction, en USD (top-of-book POC).
pub const NOTIONAL_USD: f64 = 100_000.0;

/// Commission par côté et par 100 K$ — taux négocié (6 $ aller-retour).
/// Convention canonique du document : 3,50 — recalibrable via `comm_scale >= 1.0`.
pub const COMM_PER_SIDE_USD: f64 = 3.00;

/// Coût d'un abort par jambe déjà remplie (proxy unwind : demi-spread + comm).
pub const ABORT_COST_PER_LEG_USD: f64 = 12.0;
