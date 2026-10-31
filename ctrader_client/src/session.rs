//! Fenêtre de session opérationnelle : 07:00–21:00 heure de Zurich,
//! jours ouvrés. FX s'arrête le week-end — le vendredi soir ferme à 21:00.

use chrono::{Datelike, Timelike};
use chrono_tz::Europe::Zurich;

/// Fenêtre hebdomadaire de production (heure de Zurich, inclusive).
pub const OPEN_HOUR: u32 = 7;
pub const CLOSE_HOUR: u32 = 21;

/// `ts_ms` (epoch UTC) dans la fenêtre de session Zurich ?
pub fn in_session(ts_ms: i64) -> bool {
    let Some(t) = chrono::DateTime::from_timestamp_millis(ts_ms) else {
        return false;
    };
    let z = t.with_timezone(&Zurich);
    let wd = z.weekday().num_days_from_monday(); // 0=lun .. 6=dim
    wd <= 4 && z.hour() >= OPEN_HOUR && z.hour() < CLOSE_HOUR
}

/// Secondes jusqu'à la fin de la fenêtre courante (0 si hors session)
/// — utilisé pour déclencher le flatten de fin de session.
pub fn until_close_s(ts_ms: i64) -> i64 {
    if !in_session(ts_ms) {
        return 0;
    }
    let t = chrono::DateTime::from_timestamp_millis(ts_ms).unwrap();
    let z = t.with_timezone(&Zurich);
    let close = z
        .date_naive()
        .and_hms_opt(CLOSE_HOUR, 0, 0)
        .unwrap()
        .and_local_timezone(Zurich)
        .unwrap();
    (close.timestamp_millis() - ts_ms) / 1000
}

/// Millisecondes jusqu'à la prochaine ouverture (si hors session).
/// Retourne 0 si en session. Plafond de recherche : 4 jours (pont de fête).
pub fn until_open_ms(ts_ms: i64) -> i64 {
    if in_session(ts_ms) {
        return 0;
    }
    for step in 0..=(4 * 24 * 60) {
        let t = ts_ms + step * 60_000;
        if in_session(t) {
            return t - ts_ms;
        }
    }
    i64::MAX
}

/// Secondes jusqu'à `OPEN_HOUR`-15 min : heure du "pre-flight flatten"
/// (ne pas porter de résiduel en dehors de la fenêtre).
pub fn should_flatten_now(ts_ms: i64) -> bool {
    in_session(ts_ms) && until_close_s(ts_ms) <= 15 * 60
}

#[cfg(test)]
mod tests {
    use super::*;

    // 2026-10-26 = lundi ; 2026-10-31 = samedi (CET active, UTC+1)
    const MON_12UTC: i64 = 1_793_016_000_000; // 2026-10-26 12:00 UTC = 13:00 CET
    const MON_05UTC: i64 = 1_792_990_800_000; // 2026-10-26 05:00 UTC = 06:00 CET
    const MON_19UTC: i64 = 1_793_041_200_000; // 2026-10-26 19:00 UTC = 20:00 CET
    const MON_20UTC: i64 = 1_793_044_800_000; // 2026-10-26 20:00 UTC = 21:00 CET
    const SAT_12UTC: i64 = 1_793_448_000_000; // 2026-10-31 12:00 UTC

    #[test]
    fn session_window_bounds() {
        assert!(in_session(MON_12UTC)); // 13:00 Zurich lun.
        assert!(!in_session(MON_05UTC)); // 06:00 Zurich lun.
        assert!(in_session(MON_19UTC)); // 20:00 Zurich
        assert!(!in_session(MON_20UTC)); // 21:00 pile -> fermé
        assert!(!in_session(SAT_12UTC)); // samedi
    }

    #[test]
    fn until_close_and_open() {
        // 20:00 Zurich lun. -> 3600 s avant 21:00
        assert_eq!(until_close_s(MON_19UTC), 3_600);
        // 06:00 Zurich lun. -> ouverture dans 1 h
        assert_eq!(until_open_ms(MON_05UTC), 3_600_000);
        // samedi -> prochaine ouverture lundi 07:00 (~43 h)
        let w = until_open_ms(SAT_12UTC);
        assert!(w > 40 * 3_600_000 && w < 50 * 3_600_000, "w={w}");
        // hors session -> flatten "false" sauf fenêtre de clôture
        assert!(should_flatten_now(MON_20UTC - 10 * 60_000)); // 20:50 Zurich
        assert!(!should_flatten_now(MON_19UTC));
    }

    #[test]
    fn dst_summer_cest() {
        // 2026-07-15 05:30 UTC = 07:30 CEST -> en session (UTC+2)
        let t = 1_784_094_600_000i64;
        assert!(in_session(t));
    }
}
