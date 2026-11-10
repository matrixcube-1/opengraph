//! Économie d'exécution (IC Markets cTrader).

/// Notionnel par transaction, en USD (top-of-book POC).
/// 10 K — test d'exécution réelle à taille réduite (décision utilisateur).
/// Toute l'économie est proportionnelle : net_usd, commissions, coûts
/// d'abort — le seuil `ddd_min` du config doit suivre le même facteur.
pub const NOTIONAL_USD: f64 = 10_000.0;

/// Commission par côté et par 100 K$ — calée sur les fills réels demo
/// IC Markets (10/11/2026, n=37) : médiane 3,63, max 4,00 — le nominal
/// négocié était 3,00 (6 $ A/R) ; on retient le pire cas observé.
pub const COMM_PER_100K_USD: f64 = 4.00;

/// Commission par côté et par jambe au notionnel courant — proportionnelle
/// à `NOTIONAL_USD` (10 K -> 0,40 $/côté).
pub fn comm_usd() -> f64 {
    COMM_PER_100K_USD * NOTIONAL_USD / 100_000.0
}

/// Repli du coût d'abort par jambe remplie — utilisé uniquement quand la
/// médiane de spread de l'arête est absente/non finie (audit OG2-D2 : le
/// coût réel = `med_rel x NOTIONAL + COMM`, ~23 $ sur NZDJPY vs 12 $ ici).
pub const ABORT_COST_PER_LEG_USD: f64 = 12.0;
