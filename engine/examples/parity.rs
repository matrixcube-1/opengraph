//! Parité Python↔Rust : rejoue les mêmes points d'évaluation que
//! `audit/parity_ref.py` sur le même fixture CSV et émet un JSON
//! comparable (`audit/parity_cmp.py` applique la tolérance rel 1e-9).
//!
//! Le `pi` global diffère : l'énumération Rust n'intercale pas les
//! variantes bouts du POC — la correspondance se fait via (legs, start).
//!
//! Usage: cargo run --example parity -- <fixture_dir> > rust_out.json
//!        python audit/parity_cmp.py parity_ref.json rust_out.json

use opengraph::data::{epoch_ms, load_fx_engine};
use opengraph::exec::{AbortReason, ExecOutcome};
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

    let mut items: Vec<String> = Vec::new();
    for &t in &tests_t {
        eng.sync(t);
        match eng.eval_at(t, 500.0, 1.7, 1.0, &pt) {
            None => items.push(format!("{{\"t\": {t}, \"none\": true}}")),
            Some(r) => {
                let legs: Vec<String> = pt.paths[r.path_idx]
                    .iter()
                    .map(|&e| format!("\"{}\"", FX[e as usize]))
                    .collect();
                items.push(format!(
                    "{{\"t\": {t}, \"net\": {}, \"ddd\": {}, \"legs\": [{}], \"start\": \"{}\"}}",
                    r.net_usd,
                    r.ddd_usd,
                    legs.join(","),
                    CURR[pt.p_s[r.path_idx] as usize],
                ));
                for lat in [1.3, 2.5, 5.0] {
                    // vol_guard=false : parité stricte avec le POC Python
                    let ep = opengraph::exec::ExecParams {
                        lat_ms: lat,
                        tau_slip_bp: 0.8,
                        q_min: 0.5,
                        vol_guard: false,
                    };
                    match eng.exec_seq(t, r.path_idx, &ep, &pt) {
                        None => items.push(format!(
                            "{{\"exec\": {{\"t\": {t}, \"lat\": {lat}, \"res\": null}}}}"
                        )),
                        Some(o) => {
                            let (res, pnl, fills, parts, dur) = match o {
                                ExecOutcome::Done {
                                    pnl_usd,
                                    filled_legs,
                                    partial_legs,
                                    dur_ms,
                                } => (
                                    "done".to_string(),
                                    pnl_usd,
                                    filled_legs,
                                    partial_legs,
                                    dur_ms,
                                ),
                                ExecOutcome::Abort {
                                    reason,
                                    pnl_usd,
                                    filled_legs,
                                    partial_legs,
                                    dur_ms,
                                } => (
                                    format!(
                                        "abort:{}",
                                        match reason {
                                            AbortReason::NoQuote => "no quote",
                                            AbortReason::KillIoc => "kill IOC",
                                            AbortReason::BelowQMin => "q<q_min",
                                        }
                                    ),
                                    pnl_usd,
                                    filled_legs,
                                    partial_legs,
                                    dur_ms,
                                ),
                            };
                            items.push(format!(
                                "{{\"exec\": {{\"t\": {t}, \"lat\": {lat}, \"res\": \"{res}\", \"pnl\": {pnl}, \"fills\": {fills}, \"parts\": {parts}, \"dur\": {dur}}}}}"
                            ));
                        }
                    }
                }
            }
        }
    }
    println!("{{\"evals\": [\n{}\n]}}", items.join(",\n"));
}
