//! # ctrader_client — client cTrader Open API v2 pour `opengraph`
//!
//! Chaîne : `SpotEvent -> Engine::ingest -> eval_at -> net >= ddd_min ->
//! jambes LIMIT IOC séquentielles -> exec_settle -> résiduel/unwind`.
//!
//! ## Invariants repris du moteur
//!
//! - **Chemins ouverts uniquement** — jamais de cycles fermés.
//! - **Convention A** — résiduel marqué aux prix exécutables ; unwind par
//!   clôture IOC des deltas de position créés par la séquence.
//! - **Fraîcheur / pas de futur** — décision sur le dernier tick reçu ;
//!   `t0` = horodatage venue du tick déclencheur (`SpotEvent.timestamp`
//!   si présent, sinon horloge locale NTP).
//! - **Séquentiel non atomique** — jambe N+1 n'est envoyée qu'après la
//!   résolution terminal de la jambe N (filled/cancelled/rejected/expired).
//!
//! ## Corrections documentées vs guide PDF
//!
//! - Endpoints : `demo.ctraderapi.com` / `live.ctraderapi.com`, port 5035
//!   (protobuf) — hôtes séparés, jamais mixés.
//! - `volume` = **centièmes d'unité de base** (1 lot = 10_000_000).
//! - `errorCode` = **chaîne** ("NOT_ENOUGH_MONEY"), pas un entier.
//! - Corrélation des exécutions : `order.clientOrderId` (les événements
//!   2126/2132 ne portent pas `clientMsgId`).

pub mod auth;
pub mod codec;
mod e2e_test;
pub mod config;
pub mod err;
pub mod exec;
pub mod heartbeat;
pub mod io;
pub mod journal;
pub mod net;
pub mod orders;
pub mod pending;
pub mod proto;
pub mod reconcile;
pub mod session;
pub mod spots;
pub mod symbols;

/// Taille max d'une trame acceptée (le serveur coupe au-delà : FRAME_TOO_LONG).
pub const MAX_FRAME_SIZE: usize = 1_048_576;

/// Bande IOC autour de la touche : `3 * tau_slip_bp` en fraction
/// (parité `nt_realism.py` : tol = 3*0.8 bp = 2.4 bp).
pub const IOC_TOL: f64 = 3.0 * 0.6945e-4; // 3×τ calibré (trial #87) — audit M7
