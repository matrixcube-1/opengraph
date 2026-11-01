//! Tests d'intégration bout-en-bout sur transport mémoire : la venue est
//! un `MockPeer` scripté qui vérifie chaque requête et répond sur le fil
//! réel (codec + framing + pending + dispatch + stratégie).

#![cfg(test)]

use std::borrow::Cow;
use std::time::Duration;

use quacky::MessageRead;

use crate::auth::{account_auth_req, app_auth_req, check_netted, trader_req};
use crate::codec::{decode_msg, encode_msg, envelope};
use crate::heartbeat::LastRx;
use crate::io::mock::{MemIo, MockPeer};
use crate::net::{decode_payload, dispatch_response, encode_payload, rpc, spawn, Incoming};
use crate::orders::{new_order_req, on_execution_event, FillAcc, LegResolution};
use crate::proto::spotware::*;
use crate::reconcile::{self, reconcile_req};
use crate::spots::spot_to_quote;
use crate::symbols::{fill_symbol_details, map_light_symbols};

use opengraph::engine::Engine;
use opengraph::universe::FX;

const CTID: i64 = 42;
const TIMEOUT: Duration = Duration::from_secs(3);

/// Réponse venue : encode le message métier + enveloppe avec le cmid
/// éventuel de la requête, puis injection d'une trame complète.
fn venue_reply<M: quacky::MessageWrite>(
    peer: &MockPeer,
    ptype: ProtoOAPayloadType,
    msg: &M,
    cmid: Option<&str>,
) {
    let p = encode_payload(msg)
        .unwrap_or_else(|e| panic!("encode pt={} : {e}", ptype as u32));
    let env = envelope(ptype, p, cmid);
    // send_payload ajoute l'en-tête 4B lui-même — passer l'enveloppe
    // protobuf SEULE (pas frame() : sinon double-trame, décodage désync).
    peer.send_payload(
        encode_msg(&env).unwrap_or_else(|e| panic!("env pt={} : {e}", ptype as u32)),
    );
}

/// Lit la trame client suivante et retourne l'enveloppe décodée.
async fn venue_recv(peer: &mut MockPeer) -> ProtoMessage<'static> {
    let raw = tokio::time::timeout(TIMEOUT, peer.recv_frame())
        .await
        .expect("trame client dans le délai")
        .expect("flux client ouvert");
    decode_msg(&raw[4..]).unwrap()
}

fn cmid_of(msg: &ProtoMessage<'_>) -> String {
    msg.clientMsgId.as_deref().unwrap_or("").to_string()
}

/// Serveur scripté : la séquence complète d'un bootstrap + cycle ordre.
/// Chaque étape vérifie le `payloadType` reçu avant de répondre.
async fn venue_script(peer: &mut MockPeer, symbol_ids: &[i64], first_edge: u8) {
    // 2100 app auth -> 2101
    let m = venue_recv(peer).await;
    assert_eq!(m.payloadType, 2100, "attendu APPLICATION_AUTH_REQ");
    venue_reply(
        peer,
        ProtoOAPayloadType::PROTO_OA_APPLICATION_AUTH_RES,
        &ProtoOAApplicationAuthRes { payloadType: None },
        Some(&cmid_of(&m)),
    );

    // 2102 account auth -> 2103
    let m = venue_recv(peer).await;
    assert_eq!(m.payloadType, 2102, "attendu ACCOUNT_AUTH_REQ");
    venue_reply(
        peer,
        ProtoOAPayloadType::PROTO_OA_ACCOUNT_AUTH_RES,
        &ProtoOAAccountAuthRes {
            payloadType: None,
            ctidTraderAccountId: CTID,
        },
        Some(&cmid_of(&m)),
    );

    // 2121 trader -> 2122 (NETTED)
    let m = venue_recv(peer).await;
    assert_eq!(m.payloadType, 2121, "attendu TRADER_REQ");
    venue_reply(
        peer,
        ProtoOAPayloadType::PROTO_OA_TRADER_RES,
        &ProtoOATraderRes {
            payloadType: None,
            ctidTraderAccountId: CTID,
            trader: ProtoOATrader {
                accountType: Some(ProtoOAAccountType::NETTED),
                balance: 10_000_000,
                ..Default::default()
            },
        },
        Some(&cmid_of(&m)),
    );

    // 2114 symbols list -> 2115 (28 paires FX)
    let m = venue_recv(peer).await;
    assert_eq!(m.payloadType, 2114, "attendu SYMBOLS_LIST_REQ");
    let lights: Vec<_> = FX
        .iter()
        .enumerate()
        .map(|(i, s)| ProtoOALightSymbol {
            symbolId: symbol_ids[i],
            symbolName: Some(Cow::Borrowed(*s)),
            enabled: Some(true),
            ..Default::default()
        })
        .collect();
    venue_reply(
        peer,
        ProtoOAPayloadType::PROTO_OA_SYMBOLS_LIST_RES,
        &ProtoOASymbolsListRes {
            payloadType: None,
            ctidTraderAccountId: CTID,
            symbol: lights,
            archivedSymbol: vec![],
        },
        Some(&cmid_of(&m)),
    );

    // 2116 symbol by id -> 2117 (digits/min/step par symbole)
    let m = venue_recv(peer).await;
    assert_eq!(m.payloadType, 2116, "attendu SYMBOL_BY_ID_REQ");
    let fulls: Vec<_> = symbol_ids
        .iter()
        .enumerate()
        .map(|(i, &id)| ProtoOASymbol {
            symbolId: id,
            digits: if FX[i].ends_with("JPY") { 3 } else { 5 },
            minVolume: Some(1_000),
            stepVolume: Some(100),
            ..Default::default()
        })
        .collect();
    venue_reply(
        peer,
        ProtoOAPayloadType::PROTO_OA_SYMBOL_BY_ID_RES,
        &ProtoOASymbolByIdRes {
            payloadType: None,
            ctidTraderAccountId: CTID,
            symbol: fulls,
            archivedSymbol: vec![],
        },
        Some(&cmid_of(&m)),
    );

    // 2127 subscribe -> 2128, puis SpotEvent non sollicité (edge first_edge)
    let m = venue_recv(peer).await;
    assert_eq!(m.payloadType, 2127, "attendu SUBSCRIBE_SPOTS_REQ");
    venue_reply(
        peer,
        ProtoOAPayloadType::PROTO_OA_SUBSCRIBE_SPOTS_RES,
        &ProtoOASubscribeSpotsRes {
            payloadType: None,
            ctidTraderAccountId: CTID,
        },
        Some(&cmid_of(&m)),
    );
    // prix encodés ×1e5 (échelle fixe SpotEvent, pas 10^digits)
    let px: u64 = (1.18347 * 1e5) as u64; // 118_347 -> 1.18347
    // event 1 : bid seul (cTrader n'envoie que le côté qui change)
    venue_reply(
        peer,
        ProtoOAPayloadType::PROTO_OA_SPOT_EVENT,
        &ProtoOASpotEvent {
            payloadType: None,
            ctidTraderAccountId: CTID,
            symbolId: symbol_ids[first_edge as usize],
            bid: Some(px),
            ask: None,
            timestamp: Some(1_793_016_000_000),
            ..Default::default()
        },
        None,
    );
    // event 2 : ask seul — le client doit fusionner avec le bid retenu
    venue_reply(
        peer,
        ProtoOAPayloadType::PROTO_OA_SPOT_EVENT,
        &ProtoOASpotEvent {
            payloadType: None,
            ctidTraderAccountId: CTID,
            symbolId: symbol_ids[first_edge as usize],
            bid: None,
            ask: Some(px + 3),
            timestamp: Some(1_793_016_000_001),
            ..Default::default()
        },
        None,
    );

    // 2106 new order -> ExecutionEvent 2126 (filled, deal + order)
    let m = venue_recv(peer).await;
    assert_eq!(m.payloadType, 2106, "attendu NEW_ORDER_REQ");
    let req: ProtoOANewOrderReq = {
        let p = m.payload.as_ref().unwrap();
        let mut r = quacky::BytesReader::from_bytes(p);
        ProtoOANewOrderReq::from_reader(&mut r, p).unwrap()
    };
    assert_eq!(req.ctidTraderAccountId, CTID);
    assert_eq!(
        req.timeInForce,
        Some(ProtoOATimeInForce::IMMEDIATE_OR_CANCEL),
        "les jambes doivent être IOC"
    );
    assert_eq!(req.orderType, ProtoOAOrderType::LIMIT);
    let coid = req.clientOrderId.as_deref().unwrap().to_string();
    let fill_vol = req.volume;
    let fill_px = req.limitPrice.unwrap();
    venue_reply(
        peer,
        ProtoOAPayloadType::PROTO_OA_EXECUTION_EVENT,
        &ProtoOAExecutionEvent {
            payloadType: None,
            ctidTraderAccountId: CTID,
            executionType: ProtoOAExecutionType::ORDER_FILLED,
            position: None,
            order: Some(ProtoOAOrder {
                orderId: 9_001,
                tradeData: ProtoOATradeData {
                    symbolId: req.symbolId,
                    volume: req.volume,
                    tradeSide: req.tradeSide,
                    ..Default::default()
                },
                orderType: req.orderType,
                orderStatus: ProtoOAOrderStatus::ORDER_STATUS_FILLED,
                executedVolume: Some(fill_vol),
                executionPrice: Some(fill_px),
                clientOrderId: Some(Cow::Owned(coid)),
                positionId: Some(7_001),
                ..Default::default()
            }),
            deal: Some(ProtoOADeal {
                dealId: 8_001,
                orderId: 9_001,
                positionId: 7_001,
                volume: fill_vol,
                filledVolume: fill_vol,
                symbolId: req.symbolId,
                executionPrice: Some(fill_px),
                tradeSide: req.tradeSide,
                ..Default::default()
            }),
            ..Default::default()
        },
        None,
    );

    // 2124 reconcile -> 2125 (position nette cohérente : sens du fill)
    let m = venue_recv(peer).await;
    assert_eq!(m.payloadType, 2124, "attendu RECONCILE_REQ");
    venue_reply(
        peer,
        ProtoOAPayloadType::PROTO_OA_RECONCILE_RES,
        &ProtoOAReconcileRes {
            payloadType: None,
            ctidTraderAccountId: CTID,
            position: vec![ProtoOAPosition {
                positionId: 7_001,
                tradeData: ProtoOATradeData {
                    symbolId: req.symbolId,
                    volume: fill_vol,
                    tradeSide: req.tradeSide,
                    ..Default::default()
                },
                ..Default::default()
            }],
            order: vec![],
        },
        Some(&cmid_of(&m)),
    );
}

#[tokio::test]
async fn e2e_bootstrap_spot_order_reconcile() {
    let symbol_ids: Vec<i64> = (0..28).map(|e| 100 + e as i64).collect();
    // edge utilisé pour le spot + l'ordre : EURUSD si présent sinon 0
    let edge = FX.iter().position(|&s| s == "EURUSD").unwrap_or(0) as u8;

    let (io, peer) = MemIo::pair();
    let mut handle = spawn(io, LastRx::default());
    // la venue scriptée tourne en tâche parallèle
    let venue = tokio::spawn(venue_script_inner(peer, symbol_ids.clone(), edge));

    // -- bootstrap client (vraies fonctions de la lib) --
    let res = rpc(
        &mut handle,
        ProtoOAPayloadType::PROTO_OA_APPLICATION_AUTH_REQ,
        &app_auth_req("cid", "csec"),
        TIMEOUT,
        "app auth",
    )
    .await
    .unwrap();
    assert_eq!(res.payloadType, 2101);

    let res = rpc(
        &mut handle,
        ProtoOAPayloadType::PROTO_OA_ACCOUNT_AUTH_REQ,
        &account_auth_req("tok", CTID),
        TIMEOUT,
        "account auth",
    )
    .await
    .unwrap();
    let _: ProtoOAAccountAuthRes = decode_payload(&res).unwrap();

    let res = rpc(
        &mut handle,
        ProtoOAPayloadType::PROTO_OA_TRADER_REQ,
        &trader_req(CTID),
        TIMEOUT,
        "trader",
    )
    .await
    .unwrap();
    let tr: ProtoOATraderRes = decode_payload(&res).unwrap();
    check_netted(&tr).unwrap();

    let res = rpc(
        &mut handle,
        ProtoOAPayloadType::PROTO_OA_SYMBOLS_LIST_REQ,
        &ProtoOASymbolsListReq {
            payloadType: None,
            ctidTraderAccountId: CTID,
            includeArchivedSymbols: Some(false),
        },
        TIMEOUT,
        "symbols list",
    )
    .await
    .unwrap();
    let list: ProtoOASymbolsListRes = decode_payload(&res).unwrap();
    let (mut st, ids) = map_light_symbols(&list.symbol).unwrap();
    assert_eq!(ids.len(), 28);

    let res = rpc(
        &mut handle,
        ProtoOAPayloadType::PROTO_OA_SYMBOL_BY_ID_REQ,
        &ProtoOASymbolByIdReq {
            payloadType: None,
            ctidTraderAccountId: CTID,
            symbolId: ids.clone(),
        },
        TIMEOUT,
        "symbol by id",
    )
    .await
    .unwrap();
    let full: ProtoOASymbolByIdRes = decode_payload(&res).unwrap();
    fill_symbol_details(&mut st, &full.symbol).unwrap();

    rpc(
        &mut handle,
        ProtoOAPayloadType::PROTO_OA_SUBSCRIBE_SPOTS_REQ,
        &ProtoOASubscribeSpotsReq {
            payloadType: None,
            ctidTraderAccountId: CTID,
            symbolId: st.symbol_ids(),
            subscribeToSpotTimestamp: Some(true),
        },
        TIMEOUT,
        "subscribe",
    )
    .await
    .unwrap();

    // -- SpotEvent -> Quote -> engine.ingest --
    let mut engine = engine_fixture();
    // forward-fill par edge, comme `State::last_px` de main.rs :
    // le 1er event (bid seul) ne produit pas de quote, le 2e (ask seul)
    // fusionne avec le bid retenu.
    let mut last: Option<(f64, f64)> = None;
    let quote = loop {
        match tokio::time::timeout(TIMEOUT, handle.inbound.recv())
            .await
            .unwrap()
        {
            Some(Incoming::Msg(m)) => {
                if dispatch_response(&handle, m.clone()) {
                    continue;
                }
                assert_eq!(m.payloadType, 2131);
                let ev: ProtoOASpotEvent = decode_payload(&m).unwrap();
                let (e2, si) = st.by_symbol_id(ev.symbolId).unwrap();
                assert_eq!(e2, edge);
                if let Some(q) = spot_to_quote(&ev, si, &mut last, 0) {
                    break q;
                }
            }
            other => panic!("inbound inattendu: {other:?}"),
        }
    };
    assert!((quote.bid - 1.18347).abs() < 1e-9);
    assert!((quote.ask - 1.18350).abs() < 1e-9);
    // si moteur : fx_si[edge] -> index série trié (comme main.rs)
    let si_idx = engine.fx_si[edge as usize].expect("edge mappé");
    engine.ingest(si_idx, quote);

    // -- ordre LIMIT IOC envoyé, résolution FILLED sur le fil --
    let si = st.by_edge(edge).unwrap();
    let intent = crate::orders::leg_intent(
        edge, 1, 0, 0, 90_000.0, quote.bid, quote.ask, si, "e2e-coid-1".into(),
    )
    .unwrap();
    let ord = new_order_req(CTID, si.symbol_id, &intent, "e2e");
    handle
        .outbound
        .send(owned_envelope(
            ProtoOAPayloadType::PROTO_OA_NEW_ORDER_REQ,
            encode_payload(&ord).unwrap(),
        ))
        .await
        .unwrap();

    let mut acc = FillAcc::default();
    let res = loop {
        match tokio::time::timeout(TIMEOUT, handle.inbound.recv())
            .await
            .unwrap()
        {
            Some(Incoming::Msg(m)) => {
                if dispatch_response(&handle, m.clone()) {
                    continue;
                }
                assert_eq!(m.payloadType, 2126);
                let ev: ProtoOAExecutionEvent = decode_payload(&m).unwrap();
                if let Some(r) = on_execution_event(&ev, "e2e-coid-1", &mut acc, si) {
                    break r;
                }
            }
            other => panic!("inbound inattendu: {other:?}"),
        }
    };
    let (qty, px) = match res {
        LegResolution::Filled { qty_base, px_fill, .. } => (qty_base, px_fill),
        other => panic!("attendu Filled, eu {other:?}"),
    };
    assert!((qty - intent.qty_base).abs() < 1e-9);
    assert!((px - intent.limit_px).abs() < 1e-9);

    // -- reconcile : venue déclare la position du fill -> diff cohérent --
    let res = rpc(
        &mut handle,
        ProtoOAPayloadType::PROTO_OA_RECONCILE_REQ,
        &reconcile_req(CTID),
        TIMEOUT,
        "reconcile",
    )
    .await
    .unwrap();
    let rec: ProtoOAReconcileRes = decode_payload(&res).unwrap();
    let venue_pos = reconcile::venue_positions(&rec, &st);
    // position interne : -qty si jambe vendeuse (d=+1), +qty sinon
    let internal = vec![(
        si.symbol_id,
        if intent.d == 1 { -qty } else { qty },
    )];
    assert!(reconcile::diff(&venue_pos, &internal, 1.0).is_empty());

    venue.await.unwrap();
}

/// `ProtoMessage` possédée sans cmid (ordres, souscriptions).
fn owned_envelope(ptype: ProtoOAPayloadType, payload: Vec<u8>) -> ProtoMessage<'static> {
    let env = envelope(ptype, payload, None);
    ProtoMessage {
        payloadType: env.payloadType,
        payload: env.payload.map(|c| Cow::Owned(c.into_owned())),
        clientMsgId: None,
    }
}

/// Engine live : séries vides construites sur l'univers FX (comme prod).
fn engine_fixture() -> Engine {
    Engine::live()
}

/// Wrapper : venue_script prend possession du peer.
async fn venue_script_inner(mut peer: MockPeer, symbol_ids: Vec<i64>, edge: u8) {
    venue_script(&mut peer, &symbol_ids, edge).await;
}
