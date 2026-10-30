//! # OpenGraph — moteur d'arbitrage FX à chemins ouverts
//!
//! Réimplémentation Rust de référence : `audit/poc_engine.py` (POC Python, R7).
//!
//! ## Invariants méthodologiques (non négociables)
//!
//! - **Chemins ouverts uniquement** — jamais de cycles fermés (document scellé).
//! - **Convention A** — tout résiduel est marqué aux prix exécutables
//!   (liquidation bid/ask) ; sous cette convention les terminaisons CFD sont
//!   dominées et restent hors périmètre jusqu'à validation FX en production.
//! - **Fraîcheur bilatérale** — une quote n'est valide que si
//!   `last_t <= t <= last_t + fresh_ms` ; jamais d'exposition d'une quote future.
//! - **État rembobinable** — le curseur par symbole doit pouvoir revenir en
//!   arrière (rejouabilité inter-trials, test R7-T6).
//! - **Univers Annexe J** — 28 paires FX strictes (voir [`universe`]).

pub mod data;
pub mod engine;
pub mod eval;
pub mod exec;
pub mod fees;
pub mod paths;
#[cfg(feature = "python")]
pub mod pybind;
pub mod quote;
pub mod scan;
pub mod universe;
