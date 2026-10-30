//! Exécution séquentielle E1-E9 — à implémenter (runbook étape 3).
//!
//! Référence : `poc_engine.Engine.exec_seq` + `_abort`.
//!
//! ## Sémantique requise (chaque jambe, en séquence)
//!
//! - `t_fill = t_prev + lat_ms` ; fill au **vrai tick** `<= t_fill` (jamais
//!   la quote de détection) — `no quote` sinon.
//! - Mouvement vs quote d'envoi : `move <= tau_slip` → fill complet ;
//!   `slip < move <= 3*slip` → fill partiel 60 % (résiduel marqué mid au
//!   cash) ; `move > 3*slip` → **kill IOC** → abort.
//! - `frac < q_min` → rejet → abort.
//! - Chaque fill met à jour : quantité, nœud, horodatage, commission
//!   (devise de base convertie), cash résiduel.
//! - Abort : coût `ABORT_COST_PER_LEG * jambes remplies` + résiduels.
//! - Fin : valeur USD = `qty * usd_price(node_final)` + cash − notional − comm.
//!
//! ## À spécifier au portage
//!
//! - **Garde volume top-of-book** : caper le fill à `vol` du tick utilisé
//!   (présent dans les données, absent du POC Python — écart documenté).

/// Issue d'une séquence d'exécution.
pub enum ExecOutcome {
    /// Séquence terminée.
    Done {
        pnl_usd: f64,
        filled_legs: u8,
        partial_legs: u8,
        dur_ms: i64,
    },
    /// Abandonnée en cours de route.
    Abort {
        reason: AbortReason,
        pnl_usd: f64,
        filled_legs: u8,
        partial_legs: u8,
        dur_ms: i64,
    },
}

#[derive(Clone, Copy, Debug)]
pub enum AbortReason {
    NoQuote,
    KillIoc,
    BelowQMin,
}
