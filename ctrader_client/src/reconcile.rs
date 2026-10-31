//! Rapprochement positions : `ProtoOAReconcileReq/Res` (2124/2125) vs
//! positions internes nettes par symbole.
//!
//! Compte NETTED : une position = une quantité nette par symbole (longue
//! base si volume>0 côté BUY, short si côté SELL). Notre source de vérité
//! interne = `pos_deltas` cumulés par edge. Toute divergence est
//! journalisée et devient un kill-switch soft (flatten au prochain tick).

use std::collections::{BTreeMap, BTreeSet};

use crate::proto::spotware::{ProtoOAPayloadType, ProtoOAReconcileReq, ProtoOAReconcileRes,
    ProtoOATradeSide};
use crate::symbols::SymTable;

/// Requête de rapprochement (2124).
pub fn reconcile_req(ctid: i64) -> ProtoOAReconcileReq {
    ProtoOAReconcileReq {
        payloadType: Some(ProtoOAPayloadType::PROTO_OA_RECONCILE_REQ),
        ctidTraderAccountId: ctid,
        returnProtectionOrders: Some(false),
    }
}

/// Extraction des quantités nettes venue : symbolId -> unités de base
/// signées (+ long base). `ProtoOAPosition.tradeData.volume` est en
/// centièmes d'unité, comme les ordres.
pub fn venue_positions(
    res: &ProtoOAReconcileRes<'_>,
    _st: &SymTable,
) -> Vec<(i64, f64)> {
    res.position
        .iter()
        .filter_map(|p| {
            let t = &p.tradeData;
            let sign = match t.tradeSide {
                ProtoOATradeSide::BUY => 1.0,
                ProtoOATradeSide::SELL => -1.0,
            };
            Some((t.symbolId, sign * t.volume as f64 / 100.0))
        })
        .collect()
}

/// Positions internes converties symbolId -> unités de base signées.
pub fn internal_positions(
    pos_deltas: &BTreeMap<u8, f64>,
    st: &SymTable,
) -> Vec<(i64, f64)> {
    pos_deltas
        .iter()
        .filter(|(_, &q)| q.abs() > 1e-9)
        .filter_map(|(&e, &q)| st.by_edge(e).map(|si| (si.symbol_id, q)))
        .collect()
}

/// Comparaison : tolérance `tol_units` par symbole (arrondis de pas).
/// Retourne les divergences (symbolId, venue, interne).
pub fn diff(
    venue: &[(i64, f64)],
    internal: &[(i64, f64)],
    tol_units: f64,
) -> Vec<(i64, f64, f64)> {
    let v: BTreeMap<i64, f64> = venue.iter().cloned().collect();
    let i: BTreeMap<i64, f64> = internal.iter().cloned().collect();
    let keys: BTreeSet<i64> = v.keys().chain(i.keys()).copied().collect();
    let mut out = Vec::new();
    for sid in keys {
        let a = v.get(&sid).copied().unwrap_or(0.0);
        let b = i.get(&sid).copied().unwrap_or(0.0);
        if (a - b).abs() > tol_units {
            out.push((sid, a, b));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proto::spotware::{ProtoOAPosition, ProtoOATradeData};
    use crate::symbols::SymInfo;

    fn st() -> SymTable {
        let mut info: [Option<SymInfo>; 28] = std::array::from_fn(|_| None);
        info[0] = Some(SymInfo {
            symbol_id: 11,
            digits: 5,
            min_volume: 1000,
            step_volume: 100,
            enabled: true,
        });
        SymTable { info }
    }

    fn pos(sid: i64, vol_centi: i64, side: ProtoOATradeSide) -> ProtoOAPosition<'static> {
        ProtoOAPosition {
            positionId: 1,
            tradeData: ProtoOATradeData {
                symbolId: sid,
                volume: vol_centi,
                tradeSide: side,
                ..Default::default()
            },
            ..Default::default()
        }
    }

    #[test]
    fn venue_extract_sign_and_units() {
        let res = ProtoOAReconcileRes {
            payloadType: Some(ProtoOAPayloadType::PROTO_OA_RECONCILE_RES),
            ctidTraderAccountId: 1,
            position: vec![
                pos(11, 100_000_00, ProtoOATradeSide::BUY),  // +100_000 base
                pos(22, 50_000_00, ProtoOATradeSide::SELL),  // -50_000 base
            ],
            order: vec![],
        };
        let v = venue_positions(&res, &st());
        assert_eq!(v.len(), 2);
        assert!((v[0].1 - 100_000.0).abs() < 1e-9);
        assert!((v[1].1 + 50_000.0).abs() < 1e-9);
        assert_eq!(v[1].0, 22);
    }

    #[test]
    fn diff_flags_mismatch() {
        let v = vec![(11i64, 100_000.0)];
        let i = vec![(11i64, 95_000.0)];
        let d = diff(&v, &i, 1_000.0);
        assert_eq!(d.len(), 1);
        // venue a une position, interne n'en a pas -> divergence
        let d2 = diff(&v, &[], 1_000.0);
        assert_eq!(d2.len(), 1);
        // écart < tol -> pas de divergence
        let d3 = diff(&v, &[(11, 99_500.0)], 1_000.0);
        assert!(d3.is_empty());
    }
}
