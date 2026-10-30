//! Parité Python↔Rust : rejoue les mêmes points d'évaluation que
//! `audit/parity_ref.py` sur le même fixture CSV et imprime un JSON
//! comparable (pi mappé via (legs,start), l'énumération Rust n'a pas les
//! variantes bouts intercalées du POC).
//!
//! Usage: cargo run --example parity -- <fixture_dir> <day1> <day2>

use opengraph::data::{epoch_ms, load_fx_engine};
use opengraph::exec::ExecOutcome;
use opengraph::paths::build_tables;
use opengraph::universe::{CURR, FX};

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let root = std::path::Path::new(&args[1]);
    let days = ["2026-10-22", "2026-10-23"];
    let eng = &mut load_fx_engine(root, &days);
    let pt = build_tables();

    let e1 = epoch_ms(days[1]);
    let tests_t = [
        epoch_ms(days[0]) + 6 * 3_600_000 + 60_000,
        e1 + 7 * 3_600_000 + 15_000,
        e1 + 7 * 3_600_000 + 30_000,
        e1 + 7 * 3_600_000 + 45_000,
        e1 + 7 * 3_600_000 + 90_000,
    ];

    println!("{{\"evals\": [");
    for (k, &t) in tests_t.iter().enumerate() {
        eng.sync(t);
        match eng.eval_at(t, 500.0, 1.7, 1.0, &pt) {
            None => println!("  {{\"t\": {t}, \"none\": true}}"),
            Some(r) => {
                let legs: Vec<String> = pt.paths[r.path_idx]
                    .iter()
                    .map(|&e| format!("\"{}\"", FX[e as usize]))
                    .collect();
                println!(
                    "  {{\"t\": {t}, \"net\": {}, \"ddd\": {}, \"legs\": [{}], \"start\": \"{}\"}}",
                    r.net_usd,
                    r.ddd_usd,
                    legs.join(","),
                    CURR[pt.p_s[r.path_idx] as usize],
                );
                for lat in [1.3, 2.5, 5.0] {
                    match eng.exec_seq(t, r.path_idx, lat, 0.8, 0.5, &pt) {
                        None => println!(
                            "  ,{{\"exec\": {{\"t\": {t}, \"lat\": {lat}, \"res\": null}}}}"
                        ),
                        Some(o) => {
                            let (res, pnl, fills, parts, dur) = match o {
                                ExecOutcome::Done {
                                    pnl_usd,
                                    filled_legs,
                                    partial_legs,
                                    dur_ms,
                                } => ("done", pnl_usd, filled_legs, partial_legs, dur_ms),
                                ExecOutcome::Abort {
                                    reason,
                                    pnl_usd,
                                    filled_legs,
                                    partial_legs,
                                    dur_ms,
                                } => (
                                    match reason {
                                        opengraph::exec::AbortReason::NoQuote => "abort:no quote",
                                        opengraph::exec::AbortReason::KillIoc => "abort:kill IOC",
                                        opengraph::exec::AbortReason::BelowQMin => "abort:q<q_min",
                                    },
                                    pnl_usd,
                                    filled_legs,
                                    partial_legs,
                                    dur_ms,
                                ),
                            };
                            println!(
                                "  ,{{\"exec\": {{\"t\": {t}, \"lat\": {lat}, \"res\": \"{res}\", \"pnl\": {pnl}, \"fills\": {fills}, \"parts\": {parts}, \"dur\": {dur}}}}}"
                            );
                        }
                    }
                }
            }
        }
        let _ = k;
    }
    println!("]}}");
}
