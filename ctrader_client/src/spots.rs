//! Conversion `ProtoOASpotEvent` -> `opengraph::Quote` + ingestion.
//!
//! Le flux spot cTrader n'a pas de volume top-of-book : `vol` est fixé à
//! `LIVE_VOL` (sentinelle grande mais finie) — la garde volume n'a de sens
//! que sur les données Dukascopy, pas sur les spots live (cf. exec_seq).

use opengraph::quote::Quote;

use crate::proto::spotware::ProtoOASpotEvent;
use crate::symbols::SymInfo;

/// Volume sentinelle : la garde volume sim n'a pas d'équivalent live
/// (pas de L1 affiché via l'API spots ; la profondeur exigerait 2155+).
pub const LIVE_VOL: f64 = 1.0e9;

/// Convertit un `ProtoOASpotEvent` en `Quote` moteur.
/// `bid`/`ask` ne sont envoyés QUE s'ils changent : `last` conserve le
/// dernier (bid, ask) par symbole et sert de forward-fill. `fallback_ts` =
/// horloge locale si `timestamp` venue absent (`subscribeToSpotTimestamp`
/// est demandé true, il devrait être présent).
/// Retourne None tant que les deux côtés ne sont pas connus, si la quote
/// fusionnée est invalide (bid<=0, ask<bid) ou si l'événement ne porte
/// aucun côté (trendbar seul — `last` inchangé).
pub fn spot_to_quote(
    ev: &ProtoOASpotEvent,
    si: &SymInfo,
    last: &mut Option<(f64, f64)>,
    fallback_ts: i64,
) -> Option<Quote> {
    let (b, a) = (ev.bid, ev.ask);
    if b.is_none() && a.is_none() {
        return None; // événement trendbar seul — `last` inchangé
    }
    let (lb, la) = last.unwrap_or((0.0, 0.0));
    let merged = (
        b.map(|v| si.to_px(v)).unwrap_or(lb),
        a.map(|v| si.to_px(v)).unwrap_or(la),
    );
    *last = Some(merged);
    let l = merged;
    if !(l.0.is_finite() && l.1.is_finite() && l.0 > 0.0 && l.1 >= l.0) {
        return None;
    }
    Some(Quote {
        ts_ms: ev.timestamp.unwrap_or(fallback_ts),
        bid: l.0,
        ask: l.1,
        vol: LIVE_VOL,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sym() -> SymInfo {
        SymInfo {
            symbol_id: 1,
            digits: 5,
            min_volume: 1000,
            step_volume: 100,
            enabled: true,
        }
    }

    fn ev(bid: Option<u64>, ask: Option<u64>, ts: Option<i64>) -> ProtoOASpotEvent {
        ProtoOASpotEvent {
            symbolId: 1,
            bid,
            ask,
            timestamp: ts,
            ..Default::default()
        }
    }

    // (a) premier event bid+ask -> quote
    #[test]
    fn converts_event_to_quote() {
        let mut last = None;
        let q = spot_to_quote(
            &ev(Some(118347), Some(118352), Some(1_700_000_000_123)),
            &sym(),
            &mut last,
            0,
        )
        .unwrap();
        assert_eq!(q.ts_ms, 1_700_000_000_123);
        assert!((q.bid - 1.18347).abs() < 1e-12);
        assert!((q.ask - 1.18352).abs() < 1e-12);
    }

    // (b)+(c) : events à un seul côté -> quote fusionnée avec l'historique
    #[test]
    fn one_sided_events_merge_with_last() {
        let mut last = None;
        spot_to_quote(&ev(Some(118347), Some(118352), Some(1)), &sym(), &mut last, 0);
        // bid seul : nouveau bid + ancien ask
        let q = spot_to_quote(&ev(Some(118350), None, Some(2)), &sym(), &mut last, 0)
            .unwrap();
        assert!((q.bid - 1.18350).abs() < 1e-12);
        assert!((q.ask - 1.18352).abs() < 1e-12);
        // ask seul : bid conservé + nouveau ask
        let q = spot_to_quote(&ev(None, Some(118355), Some(3)), &sym(), &mut last, 0)
            .unwrap();
        assert!((q.bid - 1.18350).abs() < 1e-12);
        assert!((q.ask - 1.18355).abs() < 1e-12);
    }

    // (d) bid seul sans historique -> None
    #[test]
    fn one_sided_without_history_returns_none() {
        let mut last = None;
        assert!(spot_to_quote(&ev(Some(118347), None, Some(1)), &sym(), &mut last, 0).is_none());
        // l'ask arrive ensuite -> quote complète
        let q = spot_to_quote(&ev(None, Some(118352), Some(2)), &sym(), &mut last, 0)
            .unwrap();
        assert!((q.bid - 1.18347).abs() < 1e-12);
        assert!((q.ask - 1.18352).abs() < 1e-12);
    }

    // (e) quote croisée après fusion -> None
    #[test]
    fn crossed_merge_returns_none() {
        let mut last = None;
        spot_to_quote(&ev(Some(118347), Some(118352), Some(1)), &sym(), &mut last, 0);
        // nouveau bid au-dessus de l'ancien ask -> croisé
        assert!(
            spot_to_quote(&ev(Some(118360), None, Some(2)), &sym(), &mut last, 0).is_none()
        );
    }

    // (f) aucun côté -> None, `last` inchangé
    #[test]
    fn no_side_returns_none_last_unchanged() {
        let mut last = Some((1.18347, 1.18352));
        assert!(spot_to_quote(&ev(None, None, Some(9)), &sym(), &mut last, 0).is_none());
        assert_eq!(last, Some((1.18347, 1.18352)));
    }

    // (g) fallback ts conservé
    #[test]
    fn missing_timestamp_uses_fallback() {
        let mut last = None;
        let q = spot_to_quote(&ev(Some(118347), Some(118352), None), &sym(), &mut last, 42)
            .unwrap();
        assert_eq!(q.ts_ms, 42);
    }
}
