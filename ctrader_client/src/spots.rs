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
/// `fallback_ts` = horloge locale si `timestamp` venue absent
/// (`subscribeToSpotTimestamp` est demandé true, il devrait être présent).
/// Retourne None si bid/ask manquent (événement trendbar seul).
pub fn spot_to_quote(
    ev: &ProtoOASpotEvent,
    si: &SymInfo,
    fallback_ts: i64,
) -> Option<Quote> {
    let bid = ev.bid?;
    let ask = ev.ask?;
    Some(Quote {
        ts_ms: ev.timestamp.unwrap_or(fallback_ts),
        bid: si.to_px(bid),
        ask: si.to_px(ask),
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

    #[test]
    fn converts_event_to_quote() {
        let ev = ProtoOASpotEvent {
            symbolId: 1,
            bid: Some(118347),
            ask: Some(118352),
            timestamp: Some(1_700_000_000_123),
            ..Default::default()
        };
        let q = spot_to_quote(&ev, &sym(), 0).unwrap();
        assert_eq!(q.ts_ms, 1_700_000_000_123);
        assert!((q.bid - 1.18347).abs() < 1e-12);
        assert!((q.ask - 1.18352).abs() < 1e-12);
    }

    #[test]
    fn missing_bid_or_ask_returns_none() {
        let ev = ProtoOASpotEvent {
            symbolId: 1,
            bid: None,
            ask: Some(118352),
            ..Default::default()
        };
        assert!(spot_to_quote(&ev, &sym(), 0).is_none());
    }

    #[test]
    fn missing_timestamp_uses_fallback() {
        let ev = ProtoOASpotEvent {
            symbolId: 1,
            bid: Some(118347),
            ask: Some(118352),
            timestamp: None,
            ..Default::default()
        };
        let q = spot_to_quote(&ev, &sym(), 42).unwrap();
        assert_eq!(q.ts_ms, 42);
    }
}
