//! Binding Python (pyo3) — feature `python`.
//!
//! Design (verrous de franchissement Python↔Rust) :
//! - **État en Rust** : séries, curseurs et tables de chemins restent côté
//!   natif ; aucune référence Python n'est retenue dans le moteur.
//! - **Un seul franchissement par tick** : `on_tick` ingère la quote, met à
//!   jour l'état et évalue — il ne renvoie qu'un signal `(pi, net, ddd)`
//!   ou `None`, jamais l'état interne.
//! - **GIL relâché** pendant le calcul (`py.allow_threads`) : l'évaluation
//!   des 2 072 chemins ne bloque pas l'interpréteur.
//! - **Même arithmétique** : le module se construit en `--release` via
//!   maturin et la parité est revérifiée à travers le binding
//!   (`audit/parity_binding.py` → `parity_cmp.py`) avant tout branchement
//!   de Strategy.

use std::path::Path;

use pyo3::prelude::*;

use crate::data::load_fx_engine;
use crate::engine::Engine;
use crate::exec::{AbortReason, ExecOutcome, ExecParams};
use crate::paths::{build_tables, PathTables};
use crate::quote::Quote;
use crate::universe::{CURR, FX};

/// Moteur exposé à Python. Deux modes :
/// - **préchargé** : `PyEngine(root, days)` charge les CSV (backtest/replay,
///   même chemin de chargement que `examples/parity.rs`), puis `eval_at(t)`
///   avance l'état à `t` et évalue ;
/// - **live** : `PyEngine.live()` crée les 28 slots FX vides, alimentés par
///   `on_tick` (ingestion + évaluation dans le même appel).
#[pyclass(name = "Engine")]
pub struct PyEngine {
    eng: Engine,
    pt: PathTables,
}

#[pymethods]
impl PyEngine {
    /// Charge les séries CSV du layout `/opt/arb/data` (28 paires FX).
    #[new]
    fn new(root: &str, days: Vec<String>) -> Self {
        let refs: Vec<&str> = days.iter().map(String::as_str).collect();
        Self {
            eng: load_fx_engine(Path::new(root), &refs),
            pt: build_tables(),
        }
    }

    /// Moteur vide pour l'ingestion live (Strategy NautilusTrader).
    #[staticmethod]
    fn live() -> Self {
        Self {
            eng: Engine::live(),
            pt: build_tables(),
        }
    }

    /// Nombre de chemins évalués (verrou : 2 072).
    fn n_paths(&self) -> usize {
        self.pt.len()
    }

    /// Symboles des jambes du chemin `pi`, ex. `["AUDCAD", "CADJPY"]`.
    fn legs(&self, pi: usize) -> Vec<String> {
        self.pt.paths[pi]
            .iter()
            .map(|&e| FX[e as usize].to_string())
            .collect()
    }

    /// Devise de départ du chemin `pi`.
    fn start(&self, pi: usize) -> String {
        CURR[self.pt.p_s[pi] as usize].to_string()
    }

    /// Un tick entrant côté Strategy : ingère la quote de l'arête FX `e`
    /// (0..28, ordre table Annexe J), publie l'état et évalue les chemins.
    /// Retourne `(path_idx, net_usd, ddd_usd)` ou `None`.
    #[allow(clippy::too_many_arguments)]
    fn on_tick(
        &mut self,
        py: Python<'_>,
        e: usize,
        t: i64,
        bid: f64,
        ask: f64,
        vol: f64,
        fresh_ms: f64,
        spread_cap: f64,
        comm_scale: f64,
    ) -> Option<(usize, f64, f64)> {
        let eng = &mut self.eng;
        let pt = &self.pt;
        py.allow_threads(move || {
            let si = eng.fx_si[e]?;
            eng.ingest(
                si,
                Quote {
                    ts_ms: t,
                    bid,
                    ask,
                    vol,
                },
            );
            eng.eval_at(t, fresh_ms, spread_cap, comm_scale, pt)
                .map(|r| (r.path_idx, r.net_usd, r.ddd_usd))
        })
    }

    /// Évalue l'état préchargé à `t` (replay/parité) : avance l'état puis
    /// calcule — équivalent à `sync(t)` + `eval_at(t)` en un appel.
    /// Retourne `(path_idx, net_usd, ddd_usd)` ou `None`.
    fn eval_at(
        &mut self,
        py: Python<'_>,
        t: i64,
        fresh_ms: f64,
        spread_cap: f64,
        comm_scale: f64,
    ) -> Option<(usize, f64, f64)> {
        let eng = &mut self.eng;
        let pt = &self.pt;
        py.allow_threads(move || {
            eng.sync(t);
            eng.eval_at(t, fresh_ms, spread_cap, comm_scale, pt)
                .map(|r| (r.path_idx, r.net_usd, r.ddd_usd))
        })
    }

    /// Exécution déterministe E1-E9 du chemin `pi` détecté à `t0` —
    /// même arithmétique que `exec.rs`.
    /// Retourne `(res, pnl_usd, fills, parts, dur_ms)` ; `res` ∈
    /// `"done"`, `"abort:no quote"`, `"abort:kill IOC"`, `"abort:q<q_min"`.
    #[allow(clippy::too_many_arguments)]
    fn exec_seq(
        &mut self,
        py: Python<'_>,
        t0: i64,
        pi: usize,
        lat_ms: f64,
        tau_slip_bp: f64,
        q_min: f64,
        vol_guard: bool,
    ) -> Option<(String, f64, u8, u8, f64)> {
        let eng = &mut self.eng;
        let pt = &self.pt;
        py.allow_threads(move || {
            let ep = ExecParams {
                lat_ms,
                tau_slip_bp,
                q_min,
                vol_guard,
            };
            eng.exec_seq(t0, pi, &ep, pt).map(|o| match o {
                ExecOutcome::Done {
                    pnl_usd,
                    filled_legs,
                    partial_legs,
                    dur_ms,
                } => (
                    "done".to_string(),
                    pnl_usd,
                    filled_legs,
                    partial_legs,
                    dur_ms,
                ),
                ExecOutcome::Abort {
                    reason,
                    pnl_usd,
                    filled_legs,
                    partial_legs,
                    dur_ms,
                } => (
                    format!(
                        "abort:{}",
                        match reason {
                            AbortReason::NoQuote => "no quote",
                            AbortReason::KillIoc => "kill IOC",
                            AbortReason::BelowQMin => "q<q_min",
                        }
                    ),
                    pnl_usd,
                    filled_legs,
                    partial_legs,
                    dur_ms,
                ),
            })
        })
    }
}

/// Module Python `opengraph` (wheel maturin, `--release`).
#[pymodule]
fn opengraph(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyEngine>()?;
    Ok(())
}
