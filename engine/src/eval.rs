//! Évaluation DDD à un instant t — à implémenter (runbook étape 3).
//!
//! Référence : `poc_engine.Engine.eval_at`.
//!
//! ## Sémantique requise
//!
//! - Pour chaque chemin : produit des taux exécutables réels (bid si le
//!   sens est base→quote, 1/ask sinon), quantité initiale
//!   `NOTIONAL / usd_price(start)`, marquage final `qty_end * usd_price(end)`.
//! - Net = valeur USD finale − notional − commissions (par jambe, devise de
//!   base) − coûts de conversion résiduels.
//! - Garde de fraîcheur bilatérale par jambe : quote absente ou
//!   `t - last_t > fresh_ms` → chemin invalide (`-inf`).
//! - `spread_cap` : quote dont le spread relatif dépasse le plafond → jambe
//!   invalide. Retourne `(net_max, path_idx, ddd)` ou None.
//! - Vectorisation : le port Rust peut réordonner l'évaluation par chemins
//!   mais doit produire un résultat identique au POC à epsilon près.

/// Résultat d'évaluation : meilleur net, indice de chemin, DDD brut.
pub struct EvalResult {
    pub net_usd: f64,
    pub path_idx: usize,
    pub ddd_usd: f64,
}
