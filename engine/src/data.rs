//! Chargement des ticks CSV layout `/opt/arb/data` — port de `load_sym`.
//!
//! ```text
//! <root>/<SYM>/<YYYY-MM-DD>/<HH>.csv
//! header: ms_off,ask,bid,ask_vol,bid_vol
//! ts_ms = epoch_ms(day) + h*3_600_000 + ms_off   (UTC)
//! avol/bvol = volumes por lado
//! ```
//!
//! Les ticks sont triés par ts de façon stable (ordre de lecture conservé
//! à timestamp égal, comme `np.argsort(kind="stable")`).

use std::path::{Path, PathBuf};

use crate::engine::{Engine, SymbolSeries};
use crate::universe::FX;

/// Jours du POC (identiques à `poc_engine.DAYS`).
pub const DAYS: [&str; 3] = ["2026-10-22", "2026-10-23", "2026-10-26"];

/// Jours depuis epoch Unix (Howard Hinnant, civil -> days).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// epoch_ms("YYYY-MM-DDT00:00:00Z").
pub fn epoch_ms(day: &str) -> i64 {
    let (y, rest) = day.split_once('-').expect("format YYYY-MM-DD");
    let (m, d) = rest.split_once('-').expect("format YYYY-MM-DD");
    days_from_civil(y.parse().unwrap(), m.parse().unwrap(), d.parse().unwrap()) * 86_400_000
}

/// Charge un symbole sur les jours donnés ; None si aucun tick.
pub fn load_symbol(root: &Path, sym: &'static str, days: &[&str]) -> Option<SymbolSeries> {
    let mut s = SymbolSeries {
        sym,
        ts: Vec::new(),
        bid: Vec::new(),
        ask: Vec::new(),
        avol: Vec::new(),
        bvol: Vec::new(),
    };
    for &day in days {
        let t0 = epoch_ms(day);
        for h in 0..24i64 {
            let f: PathBuf = root.join(sym).join(day).join(format!("{h:02}.csv"));
            let Ok(content) = std::fs::read_to_string(&f) else {
                continue; // heure absente = trou de couverture, jamais fabriqué
            };
            let base = t0 + h * 3_600_000;
            // audit OG2-B3 : lignes malformées comptées — avertissement
            // systématique, fail-fast au-delà de 5 % (corruption réelle).
            let mut total = 0usize;
            let mut rejected = 0usize;
            for line in content.lines().skip(1) {
                total += 1;
                let mut it = line.split(',');
                let (Some(ms), Some(a), Some(b), Some(av), Some(bv)) =
                    (it.next(), it.next(), it.next(), it.next(), it.next())
                else {
                    rejected += 1;
                    continue;
                };
                let (Ok(ms), Ok(a), Ok(b), Ok(av), Ok(bv)) = (
                    ms.parse::<i64>(),
                    a.parse::<f64>(),
                    b.parse::<f64>(),
                    av.parse::<f64>(),
                    bv.parse::<f64>(),
                ) else {
                    rejected += 1;
                    continue;
                };
                s.ts.push(base + ms);
                s.ask.push(a);
                s.bid.push(b);
                s.avol.push(av);
                s.bvol.push(bv);
            }
            if rejected > 0 {
                eprintln!("[data] {f:?} : {rejected}/{total} lignes rejetées (malformées)");
                if rejected >= 5 && rejected * 20 > total {
                    panic!(
                        "fichier corrompu {f:?} : {rejected}/{total} lignes rejetées"
                    );
                }
            }
        }
    }
    if s.ts.is_empty() {
        return None;
    }
    // tri stable par ts (les fichiers se recouvrent parfois aux frontières)
    let mut idx: Vec<usize> = (0..s.ts.len()).collect();
    idx.sort_by_key(|&i| s.ts[i]);
    let take = |v: &Vec<f64>| idx.iter().map(|&i| v[i]).collect();
    Some(SymbolSeries {
        sym,
        ts: idx.iter().map(|&i| s.ts[i]).collect(),
        bid: take(&s.bid),
        ask: take(&s.ask),
        avol: take(&s.avol),
        bvol: take(&s.bvol),
    })
}

/// Charge toutes les paires FX présentes sous `root` ; construit l'Engine.
pub fn load_fx_engine(root: &Path, days: &[&str]) -> Engine {
    let series: Vec<SymbolSeries> = FX
        .iter()
        .filter_map(|&s| load_symbol(root, s, days))
        .collect();
    Engine::new(series)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn civil_epoch_spot_checks() {
        assert_eq!(epoch_ms("1970-01-01"), 0);
        assert_eq!(epoch_ms("2026-10-22"), 1_792_627_200_000);
        // lendemain = +86_400_000
        assert_eq!(epoch_ms("2026-10-23") - epoch_ms("2026-10-22"), 86_400_000);
        assert_eq!(
            epoch_ms("2026-10-26") - epoch_ms("2026-10-23"),
            3 * 86_400_000
        );
    }

    #[test]
    fn load_symbol_reads_hours_and_combines_vol() {
        let dir = std::env::temp_dir().join("og_test_data");
        let f = dir.join("EURUSD").join("2026-10-22");
        std::fs::create_dir_all(&f).unwrap();
        std::fs::write(
            f.join("00.csv"),
            "ms_off,ask,bid,ask_vol,bid_vol\n0,1.1001,1.0999,2.0,1.0\n500,1.1003,1.1001,0.5,3.0\n",
        )
        .unwrap();
        std::fs::write(
            f.join("01.csv"),
            "ms_off,ask,bid,ask_vol,bid_vol\n100,1.1010,1.1008,1.5,1.5\n",
        )
        .unwrap();
        let s = load_symbol(&dir, "EURUSD", &["2026-10-22"]).unwrap();
        assert_eq!(s.len(), 3);
        assert_eq!(s.ts[0], epoch_ms("2026-10-22"));
        assert_eq!(s.ts[2], epoch_ms("2026-10-22") + 3_600_000 + 100);
        assert_eq!(s.avol[0], 2.0);
        assert_eq!(s.bvol[0], 1.0);
        assert_eq!(s.avol[1], 0.5);
        assert_eq!(s.bvol[1], 3.0);
        assert_eq!(s.ask[2], 1.1010);
        std::fs::remove_dir_all(&dir).ok();
    }

    /// Audit OG2-B3 : >5 % de lignes malformées (et >=5) = corruption ->
    /// fail-fast, jamais un trou de couverture silencieux.
    #[test]
    #[should_panic(expected = "lignes rejetées")]
    fn corrupt_csv_fails_fast_over_threshold() {
        let dir = std::env::temp_dir().join("og_test_corrupt");
        let f = dir.join("EURUSD").join("2026-10-22");
        std::fs::create_dir_all(&f).unwrap();
        let mut csv = String::from("ms_off,ask,bid,ask_vol,bid_vol\n");
        for i in 0..10 {
            csv.push_str(&format!("bad{i},x,y,z,w\n"));
        }
        csv.push_str("0,1.1001,1.0999,2.0,1.0\n"); // 10/11 rejetées -> panic
        std::fs::write(f.join("00.csv"), csv).unwrap();
        let _ = load_symbol(&dir, "EURUSD", &["2026-10-22"]);
        std::fs::remove_dir_all(&dir).ok();
    }

    /// <5 % de lignes malformées : skip + avertissement, pas fatal.
    #[test]
    fn few_malformed_lines_skipped_not_fatal() {
        let dir = std::env::temp_dir().join("og_test_warn");
        let f = dir.join("EURUSD").join("2026-10-22");
        std::fs::create_dir_all(&f).unwrap();
        let mut csv = String::from("ms_off,ask,bid,ask_vol,bid_vol\n");
        for i in 0..20 {
            csv.push_str(&format!("{i},1.1001,1.0999,2.0,1.0\n"));
        }
        csv.push_str("ligne tronquee\n"); // 1/21 < 5 % -> tolérée
        std::fs::write(f.join("00.csv"), csv).unwrap();
        let s = load_symbol(&dir, "EURUSD", &["2026-10-22"]).unwrap();
        assert_eq!(s.len(), 20);
        std::fs::remove_dir_all(&dir).ok();
    }
}
