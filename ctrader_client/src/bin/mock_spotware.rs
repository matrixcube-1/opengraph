//! `mock_spotware` — venue cTrader simulée sur TCP clair (banc d'essai).
//!
//! Parle le vrai protocole : trame 4B-BE + `ProtoMessage` protobuf, mêmes
//! `payloadType` (REQ -> RES = req+1), mêmes échelles (prix ×1e5, volume
//! en centièmes). Sert à exercer le binaire `ctrader_client` de bout en
//! bout sans la venue — auth, garde NETTED, symbols, subscribe, flux de
//! spots, warm-up, détection, reconcile.
//!
//!   mock_spotware 127.0.0.1:50350 [disloc_ms]
//!
//! Le flux : random walk ±0,3 bp/symbole à ~1,4 tick/s ; toutes les
//! `disloc_ms` (défaut 40 000) une désynchronisation −30 bp est injectée
//! sur CHFJPY pendant ~2 s — le moteur doit la voir passer `ddd_min`.

use std::borrow::Cow;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use quacky::{MessageRead, MessageWrite};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::mpsc;

use ctrader_client::codec::{decode_msg, frame_payload};
use ctrader_client::proto::spotware::*;
use opengraph::universe::FX;

const CTID: i64 = 49_404_217; // même ctid que le compte démo réel
const FRAME_MAX: usize = 1 << 20;

/// Les 7 majors USD (indices `FX` 0..6) — seules quotes à random-walk :
/// les 21 croisés sont **dérivés** pour garder un book sans arbitre en
/// baseline (les seules opportunités = les désynchronisations injectées).
const MAJORS: [usize; 7] = [0, 1, 2, 3, 4, 5, 6];

/// Valeur USD unitaire de chaque devise, dérivée des 7 majors.
fn usd_val(m: &[f64; 28], ccy: &str) -> f64 {
    match ccy {
        "USD" => 1.0,
        "EUR" => m[0],
        "GBP" => m[1],
        "JPY" => 1.0 / m[2],
        "CHF" => 1.0 / m[3],
        "AUD" => m[4],
        "CAD" => 1.0 / m[5],
        "NZD" => m[6],
        _ => unreachable!("devise hors univers"),
    }
}

/// Recalcule tous les croisés depuis les majors : `XY` = usd_val(X)/usd_val(Y).
fn derive_crosses(m: &mut [f64; 28]) {
    for i in 7..28 {
        let s = FX[i];
        m[i] = usd_val(m, &s[0..3]) / usd_val(m, &s[3..6]);
    }
}

/// Mids de départ : majors fixes, croisés dérivés → book cohérent.
fn mids() -> [f64; 28] {
    let mut m = [0.0; 28];
    m[..7].copy_from_slice(&[1.1562, 1.3120, 150.50, 0.8045, 0.6540, 1.3620, 0.5970]);
    derive_crosses(&mut m);
    m
}

fn now_ms() -> i64 {
    Utc::now().timestamp_millis()
}

/// Encode une réponse venue : payload métier + enveloppe + trame, cmid
/// répercuté (convention REQ/RES corrélée).
fn reply<M: MessageWrite>(ptype: ProtoOAPayloadType, msg: &M, cmid: Option<&str>) -> Vec<u8> {
    frame_payload(ptype, msg, cmid).expect("trame réponse encodable")
}

/// Pousse un `SpotEvent` non sollicité (pas de cmid).
fn spot(symbol_id: i64, bid: u64, ask: u64) -> Vec<u8> {
    let ev = ProtoOASpotEvent {
        payloadType: Some(ProtoOAPayloadType::PROTO_OA_SPOT_EVENT),
        ctidTraderAccountId: CTID,
        symbolId: symbol_id,
        bid: Some(bid),
        ask: Some(ask),
        timestamp: Some(now_ms()),
        ..Default::default()
    };
    frame_payload(ProtoOAPayloadType::PROTO_OA_SPOT_EVENT, &ev, None)
        .expect("spot encodable")
}

/// Générateur de flux : random walk + désynchronisation périodique CHFJPY
/// (e:24, idx 23). Envoie 4 symboles par rafale de 100 ms — chaque symbole
/// ~0,7 s entre quotes → warm-up 100 ticks en ~70 s.
async fn spot_stream(tx: mpsc::Sender<Vec<u8>>, disloc_ms: u64) {
    let chfjpy = FX.iter().position(|&s| s == "CHFJPY").unwrap();
    let mut mid = mids();
    let mut rng: u64 = 0x9E3779B97F4A7C15;
    let mut n: u64 = 0;
    let mut tick = tokio::time::interval(Duration::from_millis(100));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    loop {
        tick.tick().await;
        n += 1;
        // 4 symboles par rafale, round-robin
        // majors seules en random walk, puis croisés re-dérivés —
        // la baseline reste sans arbitre entre les bursts.
        for k in 0..4 {
            let i = MAJORS[(n * 4 + k) as usize % 7];
            // xorshift64*
            rng ^= rng >> 12;
            rng ^= rng << 25;
            rng ^= rng >> 27;
            let u = (rng.wrapping_mul(0x2545F4914F6CDD1D) >> 11) as f64 / (1u64 << 53) as f64;
            let sigma = 2e-5; // ±0,2 bp par pas
            mid[i] *= 1.0 + sigma * (2.0 * u - 1.0);
        }
        derive_crosses(&mut mid);
        for k in 0..4 {
            let i = ((n * 4 + k) as usize) % 28;
            // désynchronisation : toutes les disloc_ms, CHFJPY −30 bp ~2 s,
            // armée après 90 s de flux (warm-up terminé).
            let disloc = i == chfjpy && (n * 100) % disloc_ms < 2_000 && n * 100 > 90_000;
            let m = if disloc { mid[i] * 0.997 } else { mid[i] };
            let half = m * 0.00005; // spread ~1 bp
            let bid = ((m - half) * 1e5).round() as u64;
            let ask = ((m + half) * 1e5).round() as u64;
            if tx.send(spot(100 + i as i64, bid, ask)).await.is_err() {
                return;
            }
        }
    }
}

/// Répond à une enveloppe requête ; retourne les trames à écrire.
fn handle(m: &ProtoMessage<'_>, subscribed: &Arc<AtomicU64>) -> Vec<Vec<u8>> {
    let cmid = m.clientMsgId.as_deref();
    match m.payloadType {
        x if x == ProtoOAPayloadType::PROTO_OA_APPLICATION_AUTH_REQ as u32 => vec![reply(
            ProtoOAPayloadType::PROTO_OA_APPLICATION_AUTH_RES,
            &ProtoOAApplicationAuthRes { payloadType: None },
            cmid,
        )],
        x if x == ProtoOAPayloadType::PROTO_OA_GET_ACCOUNTS_BY_ACCESS_TOKEN_REQ as u32 => {
            vec![reply(
                ProtoOAPayloadType::PROTO_OA_GET_ACCOUNTS_BY_ACCESS_TOKEN_RES,
                &ProtoOAGetAccountListByAccessTokenRes {
                    payloadType: None,
                    accessToken: Cow::Borrowed("mock"),
                    permissionScope: None,
                    ctidTraderAccount: vec![ProtoOACtidTraderAccount {
                        ctidTraderAccountId: CTID,
                        isLive: Some(false),
                        ..Default::default()
                    }],
                },
                cmid,
            )]
        }
        x if x == ProtoOAPayloadType::PROTO_OA_ACCOUNT_AUTH_REQ as u32 => vec![reply(
            ProtoOAPayloadType::PROTO_OA_ACCOUNT_AUTH_RES,
            &ProtoOAAccountAuthRes {
                payloadType: None,
                ctidTraderAccountId: CTID,
            },
            cmid,
        )],
        x if x == ProtoOAPayloadType::PROTO_OA_TRADER_REQ as u32 => vec![reply(
            ProtoOAPayloadType::PROTO_OA_TRADER_RES,
            &ProtoOATraderRes {
                payloadType: None,
                ctidTraderAccountId: CTID,
                trader: ProtoOATrader {
                    accountType: Some(ProtoOAAccountType::NETTED),
                    balance: 100_000_00,
                    ..Default::default()
                },
            },
            cmid,
        )],
        x if x == ProtoOAPayloadType::PROTO_OA_SYMBOLS_LIST_REQ as u32 => vec![reply(
            ProtoOAPayloadType::PROTO_OA_SYMBOLS_LIST_RES,
            &ProtoOASymbolsListRes {
                payloadType: None,
                ctidTraderAccountId: CTID,
                symbol: FX
                    .iter()
                    .enumerate()
                    .map(|(i, s)| ProtoOALightSymbol {
                        symbolId: 100 + i as i64,
                        symbolName: Some(Cow::Borrowed(*s)),
                        enabled: Some(true),
                        ..Default::default()
                    })
                    .collect(),
                archivedSymbol: vec![],
            },
            cmid,
        )],
        x if x == ProtoOAPayloadType::PROTO_OA_SYMBOL_BY_ID_REQ as u32 => {
            let req: ProtoOASymbolByIdReq = {
                let p = m.payload.as_ref().expect("payload symbol_by_id");
                let mut r = quacky::BytesReader::from_bytes(p);
                ProtoOASymbolByIdReq::from_reader(&mut r, p).expect("symbol_by_id décodable")
            };
            vec![reply(
                ProtoOAPayloadType::PROTO_OA_SYMBOL_BY_ID_RES,
                &ProtoOASymbolByIdRes {
                    payloadType: None,
                    ctidTraderAccountId: CTID,
                    symbol: req
                        .symbolId
                        .iter()
                        .map(|&id| ProtoOASymbol {
                            symbolId: id,
                            digits: if FX[(id - 100) as usize].ends_with("JPY") { 3 } else { 5 },
                            minVolume: Some(1_000),
                            stepVolume: Some(100),
                            ..Default::default()
                        })
                        .collect(),
                    archivedSymbol: vec![],
                },
                cmid,
            )]
        }
        x if x == ProtoOAPayloadType::PROTO_OA_SUBSCRIBE_SPOTS_REQ as u32 => {
            subscribed.store(now_ms() as u64, Ordering::Release);
            vec![reply(
                ProtoOAPayloadType::PROTO_OA_SUBSCRIBE_SPOTS_RES,
                &ProtoOASubscribeSpotsRes {
                    payloadType: None,
                    ctidTraderAccountId: CTID,
                },
                cmid,
            )]
        }
        x if x == ProtoOAPayloadType::PROTO_OA_RECONCILE_REQ as u32 => vec![reply(
            ProtoOAPayloadType::PROTO_OA_RECONCILE_RES,
            &ProtoOAReconcileRes {
                payloadType: None,
                ctidTraderAccountId: CTID,
                position: vec![],
                order: vec![],
            },
            cmid,
        )],
        // heartbeat 51 : pas de réponse attendue (keep-alive)
        51 => vec![],
        _ => {
            tracing::warn!(ptype = m.payloadType, "requête inattendue — ignorée");
            vec![]
        }
    }
}

async fn serve(sock: TcpStream, disloc_ms: u64) -> std::io::Result<()> {
    sock.set_nodelay(true).ok();
    let (mut rd, mut wr) = tokio::io::split(sock);
    let (tx, mut rx) = mpsc::channel::<Vec<u8>>(512);
    let subscribed = Arc::new(AtomicU64::new(0));

    // écrivain : toutes les trames sortantes passent ici
    let wtx = tx.clone();
    let writer = tokio::spawn(async move {
        while let Some(f) = rx.recv().await {
            if wr.write_all(&f).await.is_err() {
                return;
            }
        }
    });

    // générateur de spots : démarre dès la souscription
    {
        let sub = subscribed.clone();
        let tx2 = tx.clone();
        tokio::spawn(async move {
            // attend la souscription puis lance le flux
            while AtomicU64::load(&sub, Ordering::Acquire) == 0 {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
            spot_stream(tx2, disloc_ms).await;
        });
    }

    // lecteur : requêtes -> réponses
    let mut len_buf = [0u8; 4];
    loop {
        if rd.read_exact(&mut len_buf).await.is_err() {
            break;
        }
        let n = u32::from_be_bytes(len_buf) as usize;
        if n == 0 || n > FRAME_MAX {
            break;
        }
        let mut buf = vec![0u8; n];
        if rd.read_exact(&mut buf).await.is_err() {
            break;
        }
        match decode_msg(&buf) {
            Ok(m) => {
                for f in handle(&m, &subscribed) {
                    if wtx.send(f).await.is_err() {
                        break;
                    }
                }
            }
            Err(e) => tracing::warn!(error = %e, "trame indécodable"),
        }
    }
    drop(wtx);
    let _ = writer.await;
    Ok(())
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    tracing_subscriber::fmt().with_env_filter("info").init();
    let addr = std::env::args().nth(1).unwrap_or_else(|| "127.0.0.1:50350".into());
    let disloc_ms: u64 = std::env::args()
        .nth(2)
        .and_then(|s| s.parse().ok())
        .unwrap_or(40_000);
    let listener = TcpListener::bind(&addr).await?;
    tracing::info!("mock spotware en écoute sur {addr} (disloc toutes les {disloc_ms} ms)");
    loop {
        let (sock, peer) = listener.accept().await?;
        tracing::info!("connexion {peer}");
        tokio::spawn(async move {
            if let Err(e) = serve(sock, disloc_ms).await {
                tracing::warn!(error = %e, "session mock terminée");
            }
        });
    }
}
