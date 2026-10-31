//! Jambes LIMIT IOC et suivi d'exécution par `clientOrderId`.
//!
//! Sémantique reprise de `nt_realism.py` (`_submit_leg` / `_resolve_leg`) :
//!
//! ```text
//! d = +1 (vente base -> quote) : SELL, px = bid*(1-tol), qty_base = acc
//! d = -1 (achat base, paie quote) : BUY, px = ask*(1+tol), qty_base = acc/ask
//! tol = 3 * tau_slip_bp * 1e-4   (= bande kill d'exec_seq)
//! frac_eff = filled_base/oq (d=+1) | filled_base*px_fill/acc (d=-1)
//! px_node  = px_fill           | 1/px_fill
//! frac_eff < q_min -> abort "q<q_min"
//! ```

use std::borrow::Cow;

use crate::proto::spotware::{
    ProtoOAExecutionType, ProtoOAExecutionEvent, ProtoOANewOrderReq, ProtoOAOrderErrorEvent,
    ProtoOAOrderType, ProtoOAPayloadType, ProtoOATimeInForce, ProtoOATradeSide,
};
use crate::symbols::SymInfo;
use crate::IOC_TOL;

/// Sens cTrader d'une jambe : `d=+1` vente de base au bid, `d=-1` achat
/// de base à l'ask (miroir `p_dir` des PathTables).
pub fn side_of(d: i8) -> ProtoOATradeSide {
    if d == 1 {
        ProtoOATradeSide::SELL
    } else {
        ProtoOATradeSide::BUY
    }
}

/// Paramètres d'envoi d'une jambe IOC — tout est résolu AVANT l'envoi ;
/// l'ordre lui-même n'emprunte rien à l'état moteur ensuite.
#[derive(Debug, Clone)]
pub struct LegIntent {
    /// `edge` FX (0..28) — pour le journal et le résiduel.
    pub edge: u8,
    /// `d` de la PathTable (+1 vente base / -1 achat base).
    pub d: i8,
    /// Devise nœud avant conversion (indice CURR) — comptabilité acc.
    pub node_from: u8,
    pub node_to: u8,
    /// Prix limite IOC (touche ± tol).
    pub limit_px: f64,
    /// Volume centièmes d'unité de base pour `ProtoOANewOrderReq.volume`.
    pub volume: i64,
    /// Quantité de base correspondante (unités) — pour `frac_eff`.
    pub qty_base: f64,
    /// `acc` de la devise nœud à l'envoi — pour `frac_eff` d=-1.
    pub acc: f64,
    /// Quote de décision utilisée (bid ou ask selon le côté).
    pub q_send: f64,
    /// `clientOrderId` unique — corrélation des ExecutionEvent.
    pub client_order_id: String,
}

/// Calcule l'intention de la jambe : prix limite ±tol, volume arrondi au
/// pas du symbole. Retourne None si la quote ou le volume est invalide
/// (abort "no quote" côté appelant).
pub fn leg_intent(
    edge: u8,
    d: i8,
    node_from: u8,
    node_to: u8,
    acc: f64,
    bid: f64,
    ask: f64,
    si: &SymInfo,
    coid: String,
) -> Option<LegIntent> {
    if !(acc.is_finite() && acc > 0.0 && bid.is_finite() && ask.is_finite() && bid > 0.0
        && ask >= bid)
    {
        return None;
    }
    let (px, qty_base, q_send) = if d == 1 {
        (bid * (1.0 - IOC_TOL), acc, bid)
    } else {
        (ask * (1.0 + IOC_TOL), acc / ask, ask)
    };
    let volume = si.to_volume(qty_base);
    if !(px.is_finite() && px > 0.0) || volume <= 0 {
        return None;
    }
    Some(LegIntent {
        edge,
        d,
        node_from,
        node_to,
        limit_px: px,
        volume,
        qty_base: si.from_volume(volume),
        acc,
        q_send,
        client_order_id: coid,
    })
}

/// Construit le `ProtoOANewOrderReq` de la jambe.
/// `label` classe l'ordre (analyse par groupe côté venue).
pub fn new_order_req<'a>(
    ctid: i64,
    symbol_id: i64,
    intent: &'a LegIntent,
    label: &'a str,
) -> ProtoOANewOrderReq<'a> {
    ProtoOANewOrderReq {
        payloadType: Some(ProtoOAPayloadType::PROTO_OA_NEW_ORDER_REQ),
        ctidTraderAccountId: ctid,
        symbolId: symbol_id,
        orderType: ProtoOAOrderType::LIMIT,
        tradeSide: side_of(intent.d),
        volume: intent.volume,
        limitPrice: Some(intent.limit_px),
        timeInForce: Some(ProtoOATimeInForce::IMMEDIATE_OR_CANCEL),
        label: Some(Cow::Borrowed(label)),
        clientOrderId: Some(Cow::Borrowed(intent.client_order_id.as_str())),
        ..Default::default()
    }
}

/// Accumulateur de fills d'un ordre (VWAP sur les événements partiels).
#[derive(Debug, Default, Clone, Copy)]
pub struct FillAcc {
    /// Volume rempli cumulé (unités de base).
    pub qty: f64,
    /// Somme qty*px — `px_fill = qty_px/qty`.
    pub qty_px: f64,
}

impl FillAcc {
    pub fn add(&mut self, qty_base: f64, px: f64) {
        self.qty += qty_base;
        self.qty_px += qty_base * px;
    }
    pub fn vwap(&self) -> f64 {
        if self.qty > 0.0 {
            self.qty_px / self.qty
        } else {
            0.0
        }
    }
}

/// Résolution terminale d'une jambe.
#[derive(Debug, Clone, PartialEq)]
pub enum LegResolution {
    /// Ordre rempli (complet ou partiel via cancel IOC) : VWAP + quantité.
    Filled { qty_base: f64, px_fill: f64, order_id: i64 },
    /// Aucun fill : cancelled/expired/rejected/error/timeout.
    Unfilled { reason: String },
}

/// Applique un `ProtoOAExecutionEvent` à un accumulateur.
/// Retourne `(est_terminal, resolution éventuelle)`.
/// `coid` filtre : seuls les événements de notre ordre comptent.
pub fn on_execution_event(
    ev: &ProtoOAExecutionEvent<'_>,
    coid: &str,
    acc: &mut FillAcc,
    si: &SymInfo,
) -> Option<LegResolution> {
    // corrélation : clientOrderId porté par le sous-objet order
    let ev_coid = ev.order.as_ref().and_then(|o| o.clientOrderId.as_deref());
    if ev_coid != Some(coid) {
        return None;
    }
    // accumule le fill de l'événement (deal privilégié).
    // `deal.volume` = volume ENVOYÉ, `filledVolume` = réellement rempli
    // (doc officielle ProtoOADeal) — prendre `max` sur-compterait les
    // deals partiels : seule la quantité exécutée compte.
    if let Some(d) = ev.deal.as_ref() {
        let qty = si.from_volume(d.filledVolume);
        let px = d.executionPrice.unwrap_or(0.0);
        if qty > 0.0 && px > 0.0 {
            acc.add(qty, px);
        }
    } else if let Some(o) = ev.order.as_ref() {
        if let (Some(v), Some(px)) = (o.executedVolume, o.executionPrice) {
            let qty = si.from_volume(v);
            if qty > acc.qty && px > 0.0 {
                // executedVolume est cumulatif — remplacer, pas ajouter
                *acc = FillAcc { qty, qty_px: qty * px };
            }
        }
    }
    let oid = ev.order.as_ref().map(|o| o.orderId).unwrap_or(0);
    // erreur métier portée par l'événement -> rejouer comme un rejet
    if ev.errorCode.is_some() && acc.qty == 0.0 {
        return Some(LegResolution::Unfilled {
            reason: format!("exec_error:{}", ev.errorCode.as_deref().unwrap_or("?")),
        });
    }
    match ev.executionType {
        ProtoOAExecutionType::ORDER_FILLED => {
            Some(LegResolution::Filled { qty_base: acc.qty, px_fill: acc.vwap(), order_id: oid })
        }
        // IOC : le reliquat est annulé -> partiel = fill partiel réel
        ProtoOAExecutionType::ORDER_CANCELLED | ProtoOAExecutionType::ORDER_EXPIRED => {
            if acc.qty > 0.0 {
                Some(LegResolution::Filled { qty_base: acc.qty, px_fill: acc.vwap(), order_id: oid })
            } else {
                Some(LegResolution::Unfilled { reason: "cancelled_no_fill".into() })
            }
        }
        ProtoOAExecutionType::ORDER_REJECTED => {
            Some(LegResolution::Unfilled {
                reason: format!("rejected:{}", ev.errorCode.as_deref().unwrap_or("?")),
            })
        }
        // ORDER_ACCEPTED / ORDER_PARTIAL_FILL / autres : non terminal
        _ => None,
    }
}

/// `ProtoOAOrderErrorEvent` -> résolution (rejet pré-booking).
/// Attribution : `orderId` si connu, sinon la jambe en vol (un seul
/// ordre actif à la fois dans l'architecture séquentielle).
pub fn on_order_error(ev: &ProtoOAOrderErrorEvent<'_>) -> (String, Option<i64>) {
    (
        format!("order_error:{}", ev.errorCode),
        ev.orderId,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proto::spotware::{
        ProtoOADeal, ProtoOAOrder, ProtoOAOrderStatus, ProtoOATradeData,
    };

    fn sym() -> SymInfo {
        SymInfo {
            symbol_id: 1,
            digits: 5,
            min_volume: 1000,
            step_volume: 100,
            enabled: true,
        }
    }

    // -- leg_intent : miroir _submit_leg --

    #[test]
    fn intent_sell_side_hits_bid_minus_tol() {
        // d=+1 : SELL, px = bid*(1-2.4bp), qty = acc (en base)
        let li = leg_intent(0, 1, 0, 1, 90_909.0, 1.18347, 1.18352, &sym(), "c1".into())
            .unwrap();
        assert_eq!(li.d, 1);
        let tol = 3.0 * 0.8e-4;
        assert!((li.limit_px - 1.18347 * (1.0 - tol)).abs() < 1e-12);
        assert_eq!(li.qty_base, 90_909.0);
        assert_eq!(li.volume, 90_909_00);
    }

    #[test]
    fn intent_buy_side_pays_ask_plus_tol() {
        // d=-1 : BUY, px = ask*(1+tol), qty_base = acc/ask
        let acc = 150_000.0; // JPY
        let ask = 1.65; // GBPCAD par ex.
        let li = leg_intent(0, -1, 3, 5, acc, 1.6498, ask, &sym(), "c2".into()).unwrap();
        let tol = 3.0 * 0.8e-4;
        assert!((li.limit_px - ask * (1.0 + tol)).abs() < 1e-12);
        // qty_base = volume réellement envoyé (arrondi au pas), miroir `oq` du POC
        let expect_vol = sym().to_volume(acc / ask);
        assert_eq!(li.volume, expect_vol);
        assert!((li.qty_base - sym().from_volume(expect_vol)).abs() < 1e-12);
    }

    #[test]
    fn intent_rejects_bad_inputs() {
        assert!(leg_intent(0, 1, 0, 1, 0.0, 1.0, 1.1, &sym(), "c".into()).is_none());
        assert!(leg_intent(0, 1, 0, 1, 1.0, f64::NAN, 1.1, &sym(), "c".into()).is_none());
        assert!(
            leg_intent(0, 1, 0, 1, 0.05, 1.0, 1.1, &sym(), "c".into()).is_none(),
            "volume sous minimum -> None"
        );
    }

    // -- on_execution_event : miroir _resolve_leg --

    fn order<'a>(coid: &'a str, executed_vol: Option<i64>, px: Option<f64>) -> ProtoOAOrder<'a> {
        ProtoOAOrder {
            orderId: 77,
            orderStatus: ProtoOAOrderStatus::ORDER_STATUS_FILLED,
            tradeData: ProtoOATradeData {
                symbolId: 1,
                volume: 0,
                tradeSide: ProtoOATradeSide::BUY,
                ..Default::default()
            },
            orderType: ProtoOAOrderType::LIMIT,
            clientOrderId: Some(Cow::Borrowed(coid)),
            executedVolume: executed_vol,
            executionPrice: px,
            ..Default::default()
        }
    }

    #[test]
    fn full_fill_resolves_with_deal() {
        let mut acc = FillAcc::default();
        let ev = ProtoOAExecutionEvent {
            executionType: ProtoOAExecutionType::ORDER_FILLED,
            order: Some(order("x1", Some(10_000_000), Some(1.1835))),
            deal: Some(ProtoOADeal {
                dealId: 1,
                orderId: 77,
                positionId: 9,
                volume: 10_000_000,
                filledVolume: 10_000_000,
                symbolId: 1,
                createTimestamp: 1,
                executionTimestamp: 2,
                executionPrice: Some(1.1835),
                tradeSide: ProtoOATradeSide::BUY,
                ..Default::default()
            }),
            ..Default::default()
        };
        let r = on_execution_event(&ev, "x1", &mut acc, &sym()).unwrap();
        assert_eq!(
            r,
            LegResolution::Filled { qty_base: 100_000.0, px_fill: 1.1835, order_id: 77 }
        );
    }

    #[test]
    fn ioc_partial_then_cancel_resolves_partial() {
        let mut acc = FillAcc::default();
        let partial = ProtoOAExecutionEvent {
            executionType: ProtoOAExecutionType::ORDER_PARTIAL_FILL,
            order: Some(order("x2", Some(6_000_000), Some(1.1835))),
            deal: Some(ProtoOADeal {
                dealId: 1,
                orderId: 77,
                positionId: 9,
                volume: 6_000_000,
                filledVolume: 6_000_000,
                symbolId: 1,
                createTimestamp: 1,
                executionTimestamp: 2,
                executionPrice: Some(1.1835),
                tradeSide: ProtoOATradeSide::BUY,
                ..Default::default()
            }),
            ..Default::default()
        };
        assert!(on_execution_event(&partial, "x2", &mut acc, &sym()).is_none());
        let cancel = ProtoOAExecutionEvent {
            executionType: ProtoOAExecutionType::ORDER_CANCELLED,
            order: Some(order("x2", Some(6_000_000), Some(1.1835))),
            ..Default::default()
        };
        let r = on_execution_event(&cancel, "x2", &mut acc, &sym()).unwrap();
        assert_eq!(
            r,
            LegResolution::Filled { qty_base: 60_000.0, px_fill: 1.1835, order_id: 77 }
        );
    }

    /// Régression C2 : `deal.volume` = volume ENVOYÉ, `filledVolume` =
    /// réellement rempli (doc officielle ProtoOADeal). Un deal partiel
    /// 6M/10M ne doit créditer que 60 000 unités — `max(volume,filled)`
    /// aurait sur-compté 100 000.
    #[test]
    fn partial_deal_credits_filled_volume_only() {
        let mut acc = FillAcc::default();
        let ev = ProtoOAExecutionEvent {
            executionType: ProtoOAExecutionType::ORDER_PARTIAL_FILL,
            order: Some(order("xp", None, None)),
            deal: Some(ProtoOADeal {
                dealId: 1,
                orderId: 77,
                positionId: 9,
                volume: 10_000_000,
                filledVolume: 6_000_000,
                symbolId: 1,
                createTimestamp: 1,
                executionTimestamp: 2,
                executionPrice: Some(1.1835),
                tradeSide: ProtoOATradeSide::BUY,
                ..Default::default()
            }),
            ..Default::default()
        };
        assert!(on_execution_event(&ev, "xp", &mut acc, &sym()).is_none());
        assert_eq!(acc.qty, 60_000.0, "seul filledVolume doit compter");
    }

    #[test]
    fn cancelled_no_fill_is_unfilled() {
        let mut acc = FillAcc::default();
        let ev = ProtoOAExecutionEvent {
            executionType: ProtoOAExecutionType::ORDER_CANCELLED,
            order: Some(order("x3", None, None)),
            ..Default::default()
        };
        assert_eq!(
            on_execution_event(&ev, "x3", &mut acc, &sym()),
            Some(LegResolution::Unfilled { reason: "cancelled_no_fill".into() })
        );
    }

    #[test]
    fn events_of_other_order_ignored() {
        let mut acc = FillAcc::default();
        let ev = ProtoOAExecutionEvent {
            executionType: ProtoOAExecutionType::ORDER_FILLED,
            order: Some(order("autre", Some(1_000_000), Some(1.1))),
            ..Default::default()
        };
        assert!(on_execution_event(&ev, "x1", &mut acc, &sym()).is_none());
    }
}
