//! `ctrader_client` — binaire production.
//!
//! ```text
//! connect TLS -> app auth -> account list -> account auth -> trader
//!   (NETTED requis) -> symbols list -> symbol by id -> subscribe spots
//! boucle : SpotEvent -> ingest -> eval_at -> (net >= ddd_min && Trade)
//!   -> PathExec séquentiel LIMIT IOC -> exec_settle -> journal
//! garde-fous : heartbeat 10 s, watchdog 35 s, reconcile 30 s,
//!   fenêtre Zurich 07-21, flatten avant 21:00, jamais hors session.
//! ```

use std::borrow::Cow;
use std::collections::BTreeMap;
use std::time::{Duration, Instant};

use chrono::Utc;

use ctrader_client::auth;
use ctrader_client::codec::envelope;
use ctrader_client::config::{load, Config, Env, Mode, Secrets};
use ctrader_client::err::{err_action, ClientError, ErrAction};
use ctrader_client::exec::{
    settle, uncovered_job, unwind_coverage, unwind_intent, LegOutcome, PathExec, ProtoTrade,
    UnwindJob, UNWIND_MAX_ATTEMPTS,
};
use ctrader_client::heartbeat::{heartbeat_msg, Backoff, HB_SEND, HB_WATCHDOG};
use ctrader_client::journal::{JEvent, Journal};
use ctrader_client::net::{
    connect, decode_payload, dispatch_response, encode_payload, rpc, spawn, Incoming,
    NetHandle,
};
use ctrader_client::orders::{
    new_order_req, on_execution_event, FillAcc, LegIntent, LegResolution,
};
use ctrader_client::proto::spotware::{
    ProtoOAErrorRes, ProtoOAExecutionEvent, ProtoOAGetAccountListByAccessTokenRes,
    ProtoOAOrderErrorEvent, ProtoOAPayloadType, ProtoOAReconcileRes, ProtoOASpotEvent,
    ProtoOASubscribeSpotsReq, ProtoOASymbolByIdReq, ProtoOASymbolByIdRes, ProtoOASymbolsListReq,
    ProtoOASymbolsListRes, ProtoOATradeSide, ProtoOATraderRes, ProtoMessage,
};
use ctrader_client::reconcile;
use ctrader_client::session::{in_session, should_flatten_now};
use ctrader_client::spots::spot_to_quote;
use ctrader_client::symbols::{fill_symbol_details, map_light_symbols, SymTable};

use opengraph::engine::Engine;
use opengraph::paths::{build_tables, PathTables};

fn now_ms() -> i64 {
    Utc::now().timestamp_millis()
}

/// Enveloppe possédée prête à envoyer (payload encodé, pas de cmid).
fn owned_envelope(ptype: ProtoOAPayloadType, payload: Vec<u8>) -> ProtoMessage<'static> {
    let env = envelope(ptype, payload, None);
    ProtoMessage {
        payloadType: env.payloadType,
        payload: env.payload.map(|c| Cow::Owned(c.into_owned())),
        clientMsgId: None,
    }
}

// ----------------------------------------------------------- bootstrap

/// Chaîne complète d'amorçage ; retourne `(ctid, SymTable)` prêts à trader.
async fn bootstrap(
    handle: &mut NetHandle,
    cfg: &Config,
    secrets: &Secrets,
    journal: &Journal,
) -> Result<(i64, SymTable), ClientError> {
    let to = Duration::from_secs(cfg.engine.req_timeout_s);

    rpc(
        handle,
        ProtoOAPayloadType::PROTO_OA_APPLICATION_AUTH_REQ,
        &auth::app_auth_req(&secrets.client_id, &secrets.client_secret),
        to,
        "app_auth",
    )
    .await?;
    journal.log(now_ms(), &JEvent::System { msg: "app_auth ok".into() });

    let mut ctid = cfg.ctid_trader_account_id;
    if ctid == 0 {
        let res = rpc(
            handle,
            ProtoOAPayloadType::PROTO_OA_GET_ACCOUNTS_BY_ACCESS_TOKEN_REQ,
            &auth::account_list_req(&secrets.access_token),
            to,
            "account_list",
        )
        .await?;
        let list: ProtoOAGetAccountListByAccessTokenRes = decode_payload(&res)?;
        ctid = auth::pick_account(&list, cfg.env == Env::Live)
            .ok_or_else(|| ClientError::Inconsistent("aucun compte compatible".into()))?;
        journal.log(now_ms(), &JEvent::System { msg: format!("ctid résolu: {ctid}") });
    }

    rpc(
        handle,
        ProtoOAPayloadType::PROTO_OA_ACCOUNT_AUTH_REQ,
        &auth::account_auth_req(&secrets.access_token, ctid),
        to,
        "account_auth",
    )
    .await?;
    journal.log(now_ms(), &JEvent::System { msg: format!("account_auth ok ctid={ctid}") });

    let res = rpc(
        handle,
        ProtoOAPayloadType::PROTO_OA_TRADER_REQ,
        &auth::trader_req(ctid),
        to,
        "trader",
    )
    .await?;
    let trader: ProtoOATraderRes = decode_payload(&res)?;
    if cfg.require_netted {
        auth::check_netted(&trader)?;
    }
    journal.log(now_ms(), &JEvent::System {
        msg: format!("trader ok accountType={:?}", trader.trader.accountType),
    });

    let res = rpc(
        handle,
        ProtoOAPayloadType::PROTO_OA_SYMBOLS_LIST_REQ,
        &ProtoOASymbolsListReq {
            payloadType: Some(ProtoOAPayloadType::PROTO_OA_SYMBOLS_LIST_REQ),
            ctidTraderAccountId: ctid,
            includeArchivedSymbols: Some(false),
        },
        to,
        "symbols_list",
    )
    .await?;
    let slist: ProtoOASymbolsListRes = decode_payload(&res)?;
    let (mut st, ids) = map_light_symbols(&slist.symbol)?;

    let res = rpc(
        handle,
        ProtoOAPayloadType::PROTO_OA_SYMBOL_BY_ID_REQ,
        &ProtoOASymbolByIdReq {
            payloadType: Some(ProtoOAPayloadType::PROTO_OA_SYMBOL_BY_ID_REQ),
            ctidTraderAccountId: ctid,
            symbolId: ids,
        },
        to,
        "symbol_by_id",
    )
    .await?;
    let full: ProtoOASymbolByIdRes = decode_payload(&res)?;
    fill_symbol_details(&mut st, &full.symbol)?;
    journal.log(now_ms(), &JEvent::System { msg: "28 symboles mappés".into() });

    rpc(
        handle,
        ProtoOAPayloadType::PROTO_OA_SUBSCRIBE_SPOTS_REQ,
        &ProtoOASubscribeSpotsReq {
            payloadType: Some(ProtoOAPayloadType::PROTO_OA_SUBSCRIBE_SPOTS_REQ),
            ctidTraderAccountId: ctid,
            symbolId: st.symbol_ids(),
            subscribeToSpotTimestamp: Some(true),
        },
        to,
        "subscribe_spots",
    )
    .await?;
    journal.log(now_ms(), &JEvent::System { msg: "spots souscrits".into() });

    Ok((ctid, st))
}

// ---------------------------------------------------------- stratégie

/// Un ordre en vol : intention + accumulateur de fills + horodatage.
struct InFlight {
    intent: LegIntent,
    fills: FillAcc,
    sent_at: Instant,
    /// `true` = ordre de clôture de résiduel, `false` = jambe de séquence.
    unwind: bool,
    edge: u8,
    trade: ProtoTrade,
    /// Essais déjà consommés (unwind — audit M6).
    attempts: u8,
}

/// Ticks ingérés par edge avant d'autoriser le trading — la médiane de
/// spread se reconstruit à chaque session (audit Mo3).
const WARMUP_TICKS: u16 = 100;

/// Sans SpotEvent depuis 30 s en session : flux suspect -> reconnect
/// (audit Mo1 — les heartbeats gardent le watchdog générique en vie).
const SPOT_WATCHDOG_MS: i64 = 30_000;

struct State {
    engine: Engine,
    pt: PathTables,
    ctid: i64,
    seq_no: u64,
    seq: Option<PathExec>,
    in_flight: Option<InFlight>,
    /// Position nette cumulée par edge (fills jambes + fills unwinds
    /// + baseline venue adoptée au premier reconcile de la session).
    net_pos: BTreeMap<u8, f64>,
    /// Clôtures IOC à émettre (abort + liquidation résiduel + flatten),
    /// une à la fois.
    unwind_queue: Vec<UnwindJob>,
    /// Nb d'ordres unwind en attente (file + en vol).
    unwind_outstanding: usize,
    /// Compteur unwind — unicité des `clientOrderId` (même edge répété).
    unwind_no: u64,
    /// `sent_ms` de la dernière jambe (lat_ms du journal).
    last_leg_sent_ms: i64,
    flatten_done_today: bool,
    /// `true` après adoption de la baseline venue (post-reconnect).
    recon_seeded: bool,
    /// Ticks ingérés par edge depuis le bootstrap (audit Mo3).
    warm_ticks: [u16; 28],
    /// Warm-up journalisé une fois (tous les edges >= WARMUP_TICKS).
    warm_logged: bool,
    /// Trading stoppé jusqu'à la prochaine session (divergence reconcile,
    /// HALT/2172 venue — audits M5/Mo1).
    halted: bool,
    /// Horodatage local du dernier SpotEvent (watchdog flux — audit Mo1).
    last_spot_ms: i64,
    /// Dernier (bid, ask) connu par edge — forward-fill des SpotEvents
    /// à un seul côté (cTrader n'envoie que le côté qui change). Reset à
    /// chaque session : le premier event après subscribe porte les deux.
    last_px: [Option<(f64, f64)>; 28],
    /// Journal dédié aux ticks (`journal_dir/ticks/`) — matière première
    /// de la calibration sur le flux venue réel (config `record_ticks`).
    tick_j: Option<Journal>,
}

impl State {
    fn new(ctid: i64, tick_dir: Option<&str>) -> Self {
        Self {
            engine: Engine::live(),
            pt: build_tables(),
            ctid,
            seq_no: 0,
            seq: None,
            in_flight: None,
            net_pos: BTreeMap::new(),
            unwind_queue: Vec::new(),
            unwind_outstanding: 0,
            unwind_no: 0,
            last_leg_sent_ms: 0,
            flatten_done_today: false,
            recon_seeded: false,
            warm_ticks: [0; 28],
            warm_logged: false,
            halted: false,
            last_spot_ms: 0,
            last_px: [None; 28],
            tick_j: tick_dir.and_then(|d| Journal::new(d).ok()),
        }
    }

    fn busy(&self) -> bool {
        self.seq.is_some() || self.in_flight.is_some() || !self.unwind_queue.is_empty()
    }

    /// Tous les edges ont reçu assez de ticks (médiane de spread fiable).
    fn warmed(&self) -> bool {
        self.warm_ticks.iter().all(|&n| n >= WARMUP_TICKS)
    }

    /// Nouvelle séquence autorisée ? — portes : session, warm-up,
    /// baseline reconcile, pas de halt, rien de busy (audits M5/Mo3).
    fn may_trade(&self, mode: Mode) -> bool {
        if mode != Mode::Trade {
            return true;
        }
        self.recon_seeded && self.warmed() && !self.halted
    }
}

/// Couverture signée des débouclages programmés (file + en vol) — audit C4.
fn coverage_now(st: &State) -> BTreeMap<u8, f64> {
    unwind_coverage(
        &st.unwind_queue,
        st.in_flight
            .as_ref()
            .filter(|f| f.unwind)
            .map(|f| (f.edge, f.trade, f.intent.qty_base)),
    )
}

/// Envoie un LegIntent sous forme de `ProtoOANewOrderReq` enveloppé.
async fn send_order(
    handle: &NetHandle,
    ctid: i64,
    symbol_id: i64,
    intent: &LegIntent,
    label: &str,
) -> Result<(), ClientError> {
    let req = new_order_req(ctid, symbol_id, intent, label);
    let env = envelope(
        ProtoOAPayloadType::PROTO_OA_NEW_ORDER_REQ,
        encode_payload(&req)?,
        None,
    );
    handle
        .outbound
        .send(ProtoMessage {
            payloadType: env.payloadType,
            payload: env.payload.map(|c| Cow::Owned(c.into_owned())),
            clientMsgId: None,
        })
        .await
}

/// Émet la jambe courante de la séquence à la quote publiée courante.
async fn fire_next_leg(
    st: &mut State,
    handle: &NetHandle,
    sym: &SymTable,
    journal: &Journal,
    mode: Mode,
) -> Result<(), ClientError> {
    let Some(seq) = st.seq.as_mut() else { return Ok(()) };
    let Some(leg) = seq.current_leg() else { return Ok(()) };
    let Some(si) = sym.by_edge(leg.edge) else {
        abort_seq(st, journal, "missing_sym");
        return Ok(());
    };
    let Some(si_idx) = st.engine.fx_si[leg.edge as usize] else {
        abort_seq(st, journal, "missing_fx_si");
        return Ok(());
    };
    let (bid, ask) = (st.engine.bid[si_idx], st.engine.ask[si_idx]);
    if bid <= 0.0 || ask <= 0.0 {
        abort_seq(st, journal, "no_quote");
        return Ok(());
    }
    let coid = format!("OG{:06}L{}", st.seq_no, seq.li);
    let Some(intent) = seq.intent_for(bid, ask, si, coid) else {
        abort_seq(st, journal, "no_intent");
        return Ok(());
    };
    if mode == Mode::Trade {
        if let Err(e) = send_order(handle, st.ctid, si.symbol_id, &intent, "og-leg").await {
            // sans abort ici, la séquence resterait `busy` à jamais
            abort_seq(st, journal, "send_fail");
            return Err(e);
        }
    }
    journal.log(now_ms(), &JEvent::LegSend {
        t0: seq.t0,
        pi: seq.pi,
        li: seq.li,
        edge: leg.edge,
        side: if leg.d == 1 { "SELL" } else { "BUY" },
        limit_px: intent.limit_px,
        volume: intent.volume,
        acc: intent.acc,
        coid: intent.client_order_id.clone(),
    });
    st.last_leg_sent_ms = now_ms();
    st.in_flight = Some(InFlight {
        intent,
        fills: FillAcc::default(),
        sent_at: Instant::now(),
        unwind: false,
        edge: leg.edge,
        trade: if leg.d == 1 { ProtoTrade::Sell } else { ProtoTrade::Buy },
        attempts: 0,
    });
    Ok(())
}

/// Abandon de la séquence : journal + settle modèle + file d'unwind.
/// Les deltas réels sont déjà dans `net_pos` (fills) — le plan d'unwind
/// vient de `seq.pos_deltas` (même contenu, scopé à la séquence).
fn abort_seq(st: &mut State, journal: &Journal, reason: &str) {
    let Some(seq) = st.seq.take() else { return };
    journal.log(now_ms(), &JEvent::LegAbort {
        t0: seq.t0,
        pi: seq.pi,
        li: seq.li,
        edge: seq.legs.get(seq.li).map(|l| l.edge).unwrap_or(0),
        reason: reason.into(),
    });
    if let Some((pnl, fl, pa, resid)) =
        settle(&mut st.engine, seq.t0, seq.pi, &seq.fills, true, &st.pt)
    {
        journal.log(now_ms(), &JEvent::Settle {
            t0: seq.t0,
            pi: seq.pi,
            pnl_usd: pnl,
            filled_legs: fl,
            partial_legs: pa,
            resid_usd: resid,
            aborted: true,
            dur_ms: now_ms() - seq.t0,
        });
    }
    let plan = seq.unwind_plan();
    enqueue_unwinds(st, plan);
}

/// Met des plans d'unwind en file (compteur + conversion en jobs).
fn enqueue_unwinds(st: &mut State, plan: Vec<(u8, ProtoTrade, f64)>) {
    st.unwind_outstanding += plan.len();
    st.unwind_queue
        .extend(plan.into_iter().map(UnwindJob::from));
}

/// Après échec d'un débouclage (timeout, rejet, IOC non rempli) :
/// re-programme le reliquat tant que `attempts < UNWIND_MAX_ATTEMPTS`,
/// sinon `manual_check` — audit M6 (une position ne doit jamais rester
/// ouverte la nuit sur un seul `manual_check` sans nouvelle tentative).
fn unwind_retry(st: &mut State, journal: &Journal, f: &InFlight, why: &str) {
    let rem = f.intent.qty_base - f.fills.qty;
    if rem <= 1e-9 {
        return;
    }
    let attempts = f.attempts + 1;
    if attempts < UNWIND_MAX_ATTEMPTS {
        st.unwind_outstanding += 1;
        // tête de file : le reliquat est prioritaire sur les plans suivants
        st.unwind_queue.insert(
            0,
            UnwindJob { edge: f.edge, trade: f.trade, qty: rem, attempts },
        );
        journal.log(now_ms(), &JEvent::VenueError {
            code: format!("unwind_retry:{why}"),
            desc: format!("edge={} rem={rem} essai={attempts}", f.edge),
            action: "retry".into(),
        });
    } else {
        journal.log(now_ms(), &JEvent::VenueError {
            code: format!("unwind_exhausted:{why}"),
            desc: format!("edge={} rem={rem}", f.edge),
            action: "manual_check".into(),
        });
    }
}

/// Émet la prochaine clôture en file (une seule à la fois — corrélation
/// simple et aucun impact sur la chaîne de décision).
async fn pump_unwind(
    st: &mut State,
    handle: &NetHandle,
    sym: &SymTable,
    journal: &Journal,
    mode: Mode,
) {
    if st.in_flight.is_some() {
        return; // un ordre à la fois
    }
    let Some(job) = st.unwind_queue.first().copied() else { return };
    let (edge, trade, qty) = (job.edge, job.trade, job.qty);
    let Some(si) = sym.by_edge(edge) else {
        st.unwind_queue.remove(0);
        st.unwind_outstanding = st.unwind_outstanding.saturating_sub(1);
        journal.log(now_ms(), &JEvent::VenueError {
            code: "unwind_no_sym".into(),
            desc: format!("edge={edge}"),
            action: "manual_check".into(),
        });
        pump_unwind_boxed(st, handle, sym, journal, mode).await;
        return;
    };
    let Some(si_idx) = st.engine.fx_si[edge as usize] else {
        st.unwind_queue.remove(0);
        st.unwind_outstanding = st.unwind_outstanding.saturating_sub(1);
        pump_unwind_boxed(st, handle, sym, journal, mode).await;
        return;
    };
    let (bid, ask) = (st.engine.bid[si_idx], st.engine.ask[si_idx]);
    if bid <= 0.0 || ask <= 0.0 {
        st.unwind_queue.remove(0);
        st.unwind_outstanding = st.unwind_outstanding.saturating_sub(1);
        journal.log(now_ms(), &JEvent::VenueError {
            code: "unwind_no_quote".into(),
            desc: format!("edge={edge} qty={qty}"),
            action: "manual_check".into(),
        });
        pump_unwind_boxed(st, handle, sym, journal, mode).await;
        return;
    }
    st.unwind_no += 1;
    let coid = format!("OG{:06}U{}-{}", st.seq_no, edge, st.unwind_no);
    let Some(intent) = unwind_intent(edge, trade, qty, bid, ask, sym, coid) else {
        st.unwind_queue.remove(0);
        st.unwind_outstanding = st.unwind_outstanding.saturating_sub(1);
        journal.log(now_ms(), &JEvent::VenueError {
            code: "unwind_no_intent".into(),
            desc: format!("edge={edge} qty={qty}"),
            action: "manual_check".into(),
        });
        pump_unwind_boxed(st, handle, sym, journal, mode).await;
        return;
    };
    if mode == Mode::Trade {
        if let Err(e) = send_order(handle, st.ctid, si.symbol_id, &intent, "og-unwind").await {
            tracing::error!(error = %e, "unwind send");
        }
    }
    journal.log(now_ms(), &JEvent::UnwindSend {
        t0: now_ms(),
        edge,
        side: if trade == ProtoTrade::Sell { "SELL" } else { "BUY" },
        qty_base: qty,
        limit_px: intent.limit_px,
        coid: intent.client_order_id.clone(),
    });
    st.in_flight = Some(InFlight {
        intent,
        fills: FillAcc::default(),
        sent_at: Instant::now(),
        unwind: true,
        edge,
        trade,
        attempts: job.attempts,
    });
    st.unwind_queue.remove(0);
}

/// Récursion async nécessite un boxing explicite.
fn pump_unwind_boxed<'a>(
    st: &'a mut State,
    handle: &'a NetHandle,
    sym: &'a SymTable,
    journal: &'a Journal,
    mode: Mode,
) -> std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send + 'a>> {
    Box::pin(pump_unwind(st, handle, sym, journal, mode))
}

/// Journalise un LegFill (partagé Advance/Done).
#[allow(clippy::too_many_arguments)]
fn journal_leg_fill(
    journal: &Journal,
    st: &State,
    li: usize,
    edge: u8,
    qty_base: f64,
    px_fill: f64,
    order_id: i64,
    frac_eff: f64,
    px_node: f64,
    comm: f64,
) {
    let Some(seq) = st.seq.as_ref() else { return };
    journal.log(now_ms(), &JEvent::LegFill {
        t0: seq.t0,
        pi: seq.pi,
        li,
        edge,
        qty_base,
        px_fill,
        frac_eff,
        px_node,
        order_id,
        lat_ms: now_ms() - st.last_leg_sent_ms,
        commission: comm,
    });
}

/// Résolution terminale d'une jambe -> transitions de la séquence.
/// `comm` = commission venue cumulée de l'ordre résolu (journal Mo8).
async fn on_leg_resolution(
    st: &mut State,
    res: LegResolution,
    handle: &NetHandle,
    sym: &SymTable,
    journal: &Journal,
    mode: Mode,
    comm: f64,
) {
    if st.seq.is_none() {
        // Séquence déjà avortée (flatten/kill) pendant le vol : la file
        // d'unwind peut contenir des jobs — ne jamais laisser repartir
        // la boucle sans pomper (audit C4 : file bloquée définitivement).
        pump_unwind(st, handle, sym, journal, mode).await;
        return;
    }
    let (li, leg_edge) = {
        let seq = st.seq.as_ref().unwrap();
        (seq.li, seq.current_leg().map(|l| l.edge).unwrap_or(0))
    };
    let filled = match &res {
        LegResolution::Filled { qty_base, px_fill, order_id } => {
            Some((*qty_base, *px_fill, *order_id))
        }
        _ => None,
    };
    let outcome = st.seq.as_mut().unwrap().apply_resolution(&res);
    match outcome {
        LegOutcome::Done => {
            let (frac, px_node) = st
                .seq
                .as_ref()
                .and_then(|s| s.fills.last().copied())
                .unwrap_or((1.0, 1.0));
            if let Some((q, p, oid)) = filled {
                journal_leg_fill(journal, st, li, leg_edge, q, p, oid, frac, px_node, comm);
            }
            let seq = st.seq.take().unwrap();
            if let Some((pnl, fl, pa, resid)) =
                settle(&mut st.engine, seq.t0, seq.pi, &seq.fills, false, &st.pt)
            {
                journal.log(now_ms(), &JEvent::Settle {
                    t0: seq.t0,
                    pi: seq.pi,
                    pnl_usd: pnl,
                    filled_legs: fl,
                    partial_legs: pa,
                    resid_usd: resid,
                    aborted: false,
                    dur_ms: now_ms() - seq.t0,
                });
                journal.log(now_ms(), &JEvent::Residual {
                    t0: seq.t0,
                    pi: seq.pi,
                    pos: seq.pos_deltas.iter().map(|(&e, &q)| (e, q)).collect(),
                    resid_usd: resid,
                });
            }
            // Convention A stricte (audit C2) : le résiduel est liquidé
            // IMMÉDIATEMENT via la file d'unwind. Le sim le marque à la
            // liquidation exécutable — le live doit réellement liquider,
            // pas porter l'exposition jusqu'au flatten de fin de session.
            let plan = seq.unwind_plan();
            enqueue_unwinds(st, plan);
            pump_unwind(st, handle, sym, journal, mode).await;
        }
        LegOutcome::Advance { frac_eff, px_node } => {
            if let Some((q, p, oid)) = filled {
                journal_leg_fill(journal, st, li, leg_edge, q, p, oid, frac_eff, px_node, comm);
            }
            if let Err(e) = fire_next_leg(st, handle, sym, journal, mode).await {
                tracing::error!(error = %e, "fire_next_leg");
            }
        }
        LegOutcome::Abort { reason } => {
            abort_seq(st, journal, &reason);
            pump_unwind(st, handle, sym, journal, mode).await;
        }
    }
}

/// Ordre en vol depuis > timeout -> résolution `Unfilled{"leg_timeout"}`.
async fn check_leg_timeout(
    st: &mut State,
    handle: &NetHandle,
    sym: &SymTable,
    journal: &Journal,
    cfg: &Config,
) {
    let too_old = st
        .in_flight
        .as_ref()
        .is_some_and(|f| f.sent_at.elapsed() > Duration::from_millis(cfg.engine.leg_timeout_ms));
    if !too_old {
        return;
    }
    let f = st.in_flight.take().unwrap();
    // Fills partiels accumulés avant le timeout : la position venue
    // existe — elle doit entrer dans net_pos (et pos_deltas pour que
    // l'unwind_plan de l'abort la couvre), sinon résiduel invisible.
    if f.fills.qty > 0.0 {
        let delta = if f.trade == ProtoTrade::Sell {
            -f.fills.qty
        } else {
            f.fills.qty
        };
        *st.net_pos.entry(f.edge).or_insert(0.0) += delta;
        if !f.unwind {
            if let Some(seq) = st.seq.as_mut() {
                *seq.pos_deltas.entry(f.edge).or_insert(0.0) += delta;
            } else {
                // Séquence déjà avortée (flatten/kill) : ce delta ne
                // figure dans AUCUN plan -> déboucler tout de suite,
                // sinon résiduel invisible jusqu'au prochain flatten
                // (audit C4 : course abort/fill).
                let trade = if delta > 0.0 { ProtoTrade::Sell } else { ProtoTrade::Buy };
                st.unwind_outstanding += 1;
                st.unwind_queue.insert(0, UnwindJob {
                    edge: f.edge,
                    trade,
                    qty: delta.abs(),
                    attempts: 0,
                });
            }
        }
        journal.log(now_ms(), &JEvent::VenueError {
            code: "timeout_partial_fill".into(),
            desc: format!("edge={} qty={} coid={}", f.edge, f.fills.qty, f.intent.client_order_id),
            action: "accounted".into(),
        });
    }
    if f.unwind {
        st.unwind_outstanding = st.unwind_outstanding.saturating_sub(1);
        unwind_retry(st, journal, &f, "timeout");
        pump_unwind(st, handle, sym, journal, cfg.mode).await;
    } else {
        on_leg_resolution(
            st,
            LegResolution::Unfilled { reason: "leg_timeout".into() },
            handle,
            sym,
            journal,
            cfg.mode,
            f.fills.comm,
        )
        .await;
    }
}

/// Applique un ExecutionEvent à l'ordre en vol (jambe ou unwind).
async fn on_exec(
    st: &mut State,
    ev: &ProtoOAExecutionEvent<'_>,
    handle: &NetHandle,
    sym: &SymTable,
    journal: &Journal,
    cfg: &Config,
) {
    let coid = ev
        .order
        .as_ref()
        .and_then(|o| o.clientOrderId.as_deref())
        .unwrap_or("")
        .to_string();
    if coid.is_empty() {
        return; // événement non corrélable (dépôt, swap, ...) — ignorer
    }
    let is_ours = st
        .in_flight
        .as_ref()
        .is_some_and(|f| f.intent.client_order_id == coid);
    if !is_ours {
        if coid.starts_with("OG") {
            // Fill tardif d'un de nos ordres (résolu par timeout/erreur
            // entre-temps) : la position venue existe — comptabiliser
            // son delta dans net_pos, sinon résiduel invisible.
            let mut accounted = false;
            if let Some(d) = ev.deal.as_ref() {
                if d.filledVolume > 0 {
                    if let Some((edge, si)) = sym.by_symbol_id(d.symbolId) {
                        let qty = si.from_volume(d.filledVolume);
                        let signed = if d.tradeSide == ProtoOATradeSide::SELL { -qty } else { qty };
                        *st.net_pos.entry(edge).or_insert(0.0) += signed;
                        accounted = true;
                        journal.log(now_ms(), &JEvent::VenueError {
                            code: "late_fill_accounted".into(),
                            desc: format!(
                                "coid={coid} edge={edge} qty={qty} side={:?}",
                                d.tradeSide
                            ),
                            action: "net_pos".into(),
                        });
                        // Fill tardif d'une JAMBE (coid « L ») : la
                        // séquence est déjà partie — ce résiduel n'est
                        // dans aucun plan -> déboucler (audit C4).
                        // Un fill tardif « U » clôt une position au
                        // contraire : comptabiliser suffit.
                        if coid.contains('L') && !coid.contains('U') {
                            let trade = if signed > 0.0 {
                                ProtoTrade::Sell
                            } else {
                                ProtoTrade::Buy
                            };
                            st.unwind_outstanding += 1;
                            st.unwind_queue.insert(0, UnwindJob {
                                edge,
                                trade,
                                qty,
                                attempts: 0,
                            });
                        }
                    }
                }
            }
            if !accounted {
                journal.log(now_ms(), &JEvent::VenueError {
                    code: "exec_no_inflight".into(),
                    desc: format!("coid={coid} type={:?}", ev.executionType),
                    action: "log".into(),
                });
            }
        }
        return;
    }
    let edge = st.in_flight.as_ref().unwrap().edge;
    let Some(si) = sym.by_edge(edge) else { return };
    let f = st.in_flight.as_mut().unwrap();
    let Some(res) = on_execution_event(ev, &coid, &mut f.fills, si) else { return };
    let f = st.in_flight.take().unwrap();

    // position nette interne : SELL base -> -qty ; BUY -> +qty
    if let LegResolution::Filled { qty_base, .. } = &res {
        let delta = if f.trade == ProtoTrade::Sell {
            -qty_base
        } else {
            *qty_base
        };
        *st.net_pos.entry(edge).or_insert(0.0) += delta;
    }

    if f.unwind {
        st.unwind_outstanding = st.unwind_outstanding.saturating_sub(1);
        match res {
            LegResolution::Filled { qty_base, px_fill, order_id } => {
                journal.log(now_ms(), &JEvent::UnwindFill {
                    t0: now_ms(),
                    edge,
                    qty_base,
                    px_fill,
                    order_id,
                    commission: f.fills.comm,
                });
                // IOC partiel : le reste de la position reste à fermer.
                let rem = f.intent.qty_base - qty_base;
                if rem > 1e-9 {
                    unwind_retry(st, journal, &f, "partial_fill");
                }
            }
            LegResolution::Unfilled { reason } => {
                unwind_retry(st, journal, &f, &format!("unfilled:{reason}"));
            }
        }
        pump_unwind(st, handle, sym, journal, cfg.mode).await;
        return;
    }
    // Séquence avortée (flatten/kill) pendant que la jambe était en vol :
    // le delta vient d'entrer dans net_pos mais n'appartient à aucun
    // pos_deltas ni plan d'unwind -> déboucler tout de suite (audit C4).
    if st.seq.is_none() {
        if let LegResolution::Filled { qty_base, .. } = &res {
            let trade = if f.trade == ProtoTrade::Sell {
                ProtoTrade::Buy
            } else {
                ProtoTrade::Sell
            };
            st.unwind_outstanding += 1;
            st.unwind_queue.insert(0, UnwindJob {
                edge,
                trade,
                qty: *qty_base,
                attempts: 0,
            });
        }
    }
    on_leg_resolution(st, res, handle, sym, journal, cfg.mode, f.fills.comm).await;
}

/// Clôture totale des positions nettes (fin de session / HALT).
/// Ne met en file que la part NON couverte par les débouclages déjà
/// programmés (audit C4 : les fills de la séquence avortée sont déjà
/// dans net_pos — sans déduplication la position serait fermée deux
/// fois, ouvrant une position inverse).
async fn flatten_all(
    st: &mut State,
    handle: &NetHandle,
    sym: &SymTable,
    journal: &Journal,
    mode: Mode,
) {
    if st.seq.is_some() {
        abort_seq(st, journal, "session_flatten");
    }
    let cov = coverage_now(st);
    let extra: Vec<UnwindJob> = st
        .net_pos
        .iter()
        .filter_map(|(&e, &q)| uncovered_job(e, q, cov.get(&e).copied().unwrap_or(0.0)))
        .collect();
    st.unwind_outstanding += extra.len();
    st.unwind_queue.extend(extra);
    pump_unwind(st, handle, sym, journal, mode).await;
}

// ---------------------------------------------------------- dispatch

async fn handle_frame(
    st: &mut State,
    msg: ProtoMessage<'static>,
    handle: &NetHandle,
    sym: &SymTable,
    journal: &Journal,
    cfg: &Config,
) -> Result<(), ClientError> {
    match msg.payloadType {
        2131 => {
            let Ok(ev) = decode_payload::<ProtoOASpotEvent>(&msg) else { return Ok(()) };
            let Some((edge, si)) = sym.by_symbol_id(ev.symbolId) else { return Ok(()) };
            let Some(q) = spot_to_quote(
                &ev,
                si,
                &mut st.last_px[edge as usize],
                now_ms(),
            ) else { return Ok(()) };
            let Some(si_idx) = st.engine.fx_si[edge as usize] else { return Ok(()) };
            let t = q.ts_ms;
            st.engine.ingest(si_idx, q);
            st.last_spot_ms = now_ms();
            // Capture du flux venue (avant toutes les portes) : calibration
            // sur données réelles — e = indice FX, ts = timestamp venue.
            if let Some(tj) = &st.tick_j {
                tj.log(t, &JEvent::Tick { e: edge, bid: q.bid, ask: q.ask });
            }
            if st.warm_ticks[edge as usize] < WARMUP_TICKS {
                st.warm_ticks[edge as usize] += 1;
                if st.warmed() && !st.warm_logged {
                    st.warm_logged = true;
                    journal.log(now_ms(), &JEvent::System {
                        msg: format!("warm-up complet ({WARMUP_TICKS} ticks/symbole)"),
                    });
                }
            }
            // Pas de nouvelle séquence dans la fenêtre de flatten : un
            // chemin ouvert à 20:55 serait tué à mi-parcours à 21:00.
            // may_trade : baseline reconcile adoptée + warm-up + pas de
            // halt (audits M5/Mo3) — detect n'a pas ces portes.
            if !in_session(t) || should_flatten_now(t) || st.busy()
                || !st.may_trade(cfg.mode)
            {
                return Ok(());
            }
            let Some(r) = st.engine.eval_at(
                t,
                cfg.engine.fresh_ms,
                cfg.engine.spread_cap,
                cfg.engine.comm_scale,
                &st.pt,
            ) else { return Ok(()) };
            if r.net_usd < cfg.engine.ddd_min {
                return Ok(());
            }
            journal.log(t, &JEvent::Opportunity {
                t0: t,
                pi: r.path_idx,
                net_usd: r.net_usd,
                ddd_usd: r.ddd_usd,
                start: st.pt.p_s[r.path_idx],
                legs: st.pt.p_l[r.path_idx],
                traded: cfg.mode == Mode::Trade,
            });
            if cfg.mode != Mode::Trade {
                return Ok(());
            }
            let Some(acc) = st.engine.start_qty(r.path_idx, &st.pt) else { return Ok(()) };
            st.seq_no += 1;
            st.seq = Some(PathExec::new(t, r.path_idx, acc, cfg.engine.q_min, &st.pt));
            if let Err(e) = fire_next_leg(st, handle, sym, journal, cfg.mode).await {
                tracing::error!(error = %e, "fire leg0");
            }
        }
        2126 => {
            let Ok(ev) = decode_payload::<ProtoOAExecutionEvent>(&msg) else { return Ok(()) };
            on_exec(st, &ev, handle, sym, journal, cfg).await;
        }
        2132 => {
            let Ok(ev) = decode_payload::<ProtoOAOrderErrorEvent>(&msg) else { return Ok(()) };
            journal.log(now_ms(), &JEvent::VenueError {
                code: ev.errorCode.to_string(),
                desc: ev.description.as_deref().unwrap_or("").into(),
                action: format!("{:?}", err_action(&ev.errorCode)),
            });
            if st.in_flight.is_some() {
                let f = st.in_flight.take().unwrap();
                // Fills partiels avant l'erreur : la position existe —
                // comptabiliser comme le chemin timeout (audit Mo5).
                if f.fills.qty > 0.0 {
                    let delta = if f.trade == ProtoTrade::Sell {
                        -f.fills.qty
                    } else {
                        f.fills.qty
                    };
                    *st.net_pos.entry(f.edge).or_insert(0.0) += delta;
                    if !f.unwind {
                        if let Some(seq) = st.seq.as_mut() {
                            *seq.pos_deltas.entry(f.edge).or_insert(0.0) += delta;
                        } else {
                            let tr = if delta > 0.0 { ProtoTrade::Sell } else { ProtoTrade::Buy };
                            st.unwind_outstanding += 1;
                            st.unwind_queue.insert(0, UnwindJob {
                                edge: f.edge,
                                trade: tr,
                                qty: delta.abs(),
                                attempts: 0,
                            });
                        }
                    }
                    journal.log(now_ms(), &JEvent::VenueError {
                        code: "order_error_partial_fill".into(),
                        desc: format!("edge={} qty={}", f.edge, f.fills.qty),
                        action: "accounted".into(),
                    });
                }
                if f.unwind {
                    st.unwind_outstanding = st.unwind_outstanding.saturating_sub(1);
                    unwind_retry(st, journal, &f, &format!("order_error:{}", ev.errorCode));
                    pump_unwind(st, handle, sym, journal, cfg.mode).await;
                } else {
                    on_leg_resolution(
                        st,
                        LegResolution::Unfilled {
                            reason: format!("order_error:{}", ev.errorCode),
                        },
                        handle,
                        sym,
                        journal,
                        cfg.mode,
                        f.fills.comm,
                    )
                    .await;
                }
            }
        }
        2142 => {
            let Ok(ev) = decode_payload::<ProtoOAErrorRes>(&msg) else { return Ok(()) };
            let act = err_action(&ev.errorCode);
            journal.log(now_ms(), &JEvent::VenueError {
                code: ev.errorCode.to_string(),
                desc: ev.description.as_deref().unwrap_or("").into(),
                action: format!("{act:?}"),
            });
            match act {
                ErrAction::Halt | ErrAction::CloseOnly => {
                    st.halted = true;
                    flatten_all(st, handle, sym, journal, cfg.mode).await;
                }
                ErrAction::ReAuth | ErrAction::RefreshToken => {
                    // Session morte côté compte : forcer la reconnexion
                    // (bootstrap re-auth) — journaliser seul laissait la
                    // session silencieusement invalide (audit Mo1).
                    return Err(ClientError::Dead(format!(
                        "reauth venue:{}", ev.errorCode
                    )));
                }
                _ => {}
            }
        }
        // La venue a terminé la session du compte : aucune réponse
        // n'arrivera plus — reconnexion complète (audit Mo1).
        2147 | 2164 => {
            journal.log(now_ms(), &JEvent::System {
                msg: format!("session compte terminée par la venue (pt {})", msg.payloadType),
            });
            return Err(ClientError::Dead(format!(
                "account session event {}", msg.payloadType
            )));
        }
        // Margin call déclenché : halt + flatten (une seule fois —
        // flatten_all déduplique déjà contre la file, audit C4).
        2172 => {
            journal.log(now_ms(), &JEvent::System {
                msg: "MARGIN_CALL_TRIGGER — halt + flatten".into(),
            });
            st.halted = true;
            flatten_all(st, handle, sym, journal, cfg.mode).await;
        }
        2125 => {
            let Ok(res) = decode_payload::<ProtoOAReconcileRes>(&msg) else { return Ok(()) };
            let venue = reconcile::venue_positions(&res, sym);
            if !st.recon_seeded {
                // Premier reconcile de la session : baseline venue —
                // les positions survivantes d'une session précédente
                // (reconnect, crash) entrent dans net_pos, sinon elles
                // resteraient orphelines jusqu'au prochain flatten.
                st.recon_seeded = true;
                let mut adopted = 0usize;
                for &(sym_id, q) in &venue {
                    if let Some((edge, _)) = sym.by_symbol_id(sym_id) {
                        if q.abs() > 1e-9 {
                            *st.net_pos.entry(edge).or_insert(0.0) += q;
                            adopted += 1;
                        }
                    }
                }
                journal.log(now_ms(), &JEvent::System {
                    msg: format!("baseline venue adoptée ({adopted} positions)"),
                });
            }
            let internal = reconcile::internal_positions(&st.net_pos, sym);
            let divergent = !reconcile::diff(&venue, &internal, 1_000.0).is_empty();
            journal.log(now_ms(), &JEvent::Reconcile { venue, internal, divergent });
            if divergent {
                if st.busy() {
                    // Transient attendu : fills en vol / file d'unwind
                    // non synchronisée avec la venue -> journaliser seul.
                    tracing::warn!("reconcile divergent (busy) — probable transitoire");
                } else {
                    // Divergence à l'arrêt = état interne incertain :
                    // kill-switch soft — plus de nouveaux trades et on
                    // aplatit ce que l'on sait (audit M5).
                    st.halted = true;
                    journal.log(now_ms(), &JEvent::System {
                        msg: "reconcile divergent à l'arrêt — halt + flatten".into(),
                    });
                    flatten_all(st, handle, sym, journal, cfg.mode).await;
                }
            }
        }
        51 => {}
        _ => {
            let pt = msg.payloadType;
            if !dispatch_response(handle, msg) {
                tracing::debug!(pt, "payload non routé");
            }
        }
    }
    Ok(())
}

// --------------------------------------------------------------- main

/// Tâches périodiques — appelées après CHAQUE itération de la boucle :
/// sous un flot continu de ticks la branche `select!` inbound gagnerait
/// toujours et heartbeats/timeouts/reconcile/flatten seraient affamés.
async fn housekeeping(
    st: &mut State,
    handle: &NetHandle,
    sym: &SymTable,
    journal: &Journal,
    cfg: &Config,
    last_hb: &mut Instant,
    last_rec: &mut Instant,
) -> Result<(), ClientError> {
    if last_hb.elapsed() >= HB_SEND {
        *last_hb = Instant::now();
        let _ = handle.outbound.send(heartbeat_msg()).await;
    }
    if let Some(age) = handle.last_rx.age() {
        if age > HB_WATCHDOG {
            return Err(ClientError::Dead(format!("watchdog {age:?}")));
        }
    }
    // Watchdog du FLUX de spots : les heartbeats maintiennent le
    // watchdog générique en vie même si le compte est déconnecté —
    // en session, aucun spot depuis 30 s = flux suspect (audit Mo1).
    if st.last_spot_ms > 0 && in_session(now_ms())
        && now_ms() - st.last_spot_ms > SPOT_WATCHDOG_MS
    {
        return Err(ClientError::Dead("spot watchdog 30s en session".into()));
    }
    check_leg_timeout(st, handle, sym, journal, cfg).await;
    // Filet de sécurité (audit C4) : toute résolution/erreur appelle
    // déjà pump_unwind ; ce repompage couvre les chemins oubliés —
    // une file non vide sans ordre en vol ne doit jamais rester bloquée.
    if st.seq.is_none() && st.in_flight.is_none() && !st.unwind_queue.is_empty() {
        pump_unwind(st, handle, sym, journal, cfg.mode).await;
    }
    if last_rec.elapsed() >= Duration::from_secs(cfg.reconcile_s) {
        *last_rec = Instant::now();
        let req = reconcile::reconcile_req(st.ctid);
        if let Ok(p) = encode_payload(&req) {
            let _ = handle
                .outbound
                .send(owned_envelope(ProtoOAPayloadType::PROTO_OA_RECONCILE_REQ, p))
                .await;
        }
    }
    let t = now_ms();
    if should_flatten_now(t) && !st.flatten_done_today {
        st.flatten_done_today = true;
        journal.log(t, &JEvent::System { msg: "flatten fin de session".into() });
        flatten_all(st, handle, sym, journal, cfg.mode).await;
    }
    if !in_session(t) {
        st.flatten_done_today = false;
    }
    Ok(())
}

async fn run_session(
    handle: &mut NetHandle,
    cfg: &Config,
    secrets: &Secrets,
    journal: &Journal,
) -> Result<(), ClientError> {
    let (ctid, sym) = bootstrap(handle, cfg, secrets, journal).await?;
    let mut st = State::new(
        ctid,
        cfg.record_ticks
            .then(|| format!("{}/ticks", cfg.journal_dir))
            .as_deref(),
    );
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut last_hb = Instant::now();
    let mut last_rec = Instant::now();

    // Reconcile immédiat : baseline venue (positions survivantes d'une
    // session précédente) avant tout ordre — voir arm 2125 (recon_seeded).
    let req = reconcile::reconcile_req(ctid);
    if let Ok(p) = encode_payload(&req) {
        let _ = handle
            .outbound
            .send(owned_envelope(ProtoOAPayloadType::PROTO_OA_RECONCILE_REQ, p))
            .await;
    }

    loop {
        tokio::select! {
            inc = handle.inbound.recv() => match inc {
                Some(Incoming::Msg(m)) => {
                    handle_frame(&mut st, m, handle, &sym, journal, cfg).await?;
                }
                Some(Incoming::Dead(d)) => return Err(ClientError::Dead(d)),
                None => return Err(ClientError::Dead("inbound fermé".into())),
            },
            _ = tick.tick() => {},
        }
        housekeeping(&mut st, handle, &sym, journal, cfg, &mut last_hb, &mut last_rec).await?;
    }
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let cfg_path = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "config/ctrader.toml".to_string());
    let cfg = match load(&cfg_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("config: {e}");
            std::process::exit(2);
        }
    };
    let secrets = match Secrets::from_env() {
        Ok(s) => s,
        Err(e) => {
            eprintln!("secrets: {e}");
            std::process::exit(2);
        }
    };
    let journal = match Journal::new(&cfg.journal_dir) {
        Ok(j) => j,
        Err(e) => {
            eprintln!("journal: {e}");
            std::process::exit(2);
        }
    };
    journal.log(now_ms(), &JEvent::System {
        msg: format!("boot env={:?} mode={:?} ddd={}", cfg.env, cfg.mode, cfg.engine.ddd_min),
    });

    let mut backoff = Backoff::new();
    let mut failures = 0u32;
    loop {
        let last_rx = ctrader_client::heartbeat::LastRx::default();
        let io = match connect(cfg.env, Duration::from_secs(10)).await {
            Ok(io) => io,
            Err(e) => {
                failures += 1;
                journal.log(now_ms(), &JEvent::System {
                    msg: format!("connect fail #{failures}: {e}"),
                });
                if failures >= 5 {
                    journal.log(now_ms(), &JEvent::System {
                        msg: "circuit breaker: arrêt".into(),
                    });
                    return;
                }
                tokio::time::sleep(backoff.next_delay()).await;
                continue;
            }
        };
        let mut handle = spawn(io, last_rx);
        let session_start = Instant::now();
        match run_session(&mut handle, &cfg, &secrets, &journal).await {
            Ok(()) => return,
            Err(e) => {
                // Session ayant vécu > 60 s : compteurs remisés — le
                // breaker ne doit compter que les échecs consécutifs,
                // pas le cumul depuis le lancement (audit Mo4).
                if session_start.elapsed() > Duration::from_secs(60) {
                    failures = 0;
                    backoff.reset();
                }
                failures += 1;
                journal.log(now_ms(), &JEvent::System {
                    msg: format!("session morte #{failures}: {e} — reconnect"),
                });
                if failures >= 10 {
                    journal.log(now_ms(), &JEvent::System {
                        msg: "circuit breaker: arrêt".into(),
                    });
                    return;
                }
                tokio::time::sleep(backoff.next_delay()).await;
            }
        }
    }
}
