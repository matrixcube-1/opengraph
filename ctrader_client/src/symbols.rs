//! Table des symboles : mapping `FX` opengraph <-> `symbolId` cTrader,
//! digits, conversion prix/volume.
//!
//! Rappel unités (vérifié doc officielle — le guide PDF se trompe) :
//! - `bid`/`ask` : `u64` en `10^digits` unités de prix ;
//! - `volume` : **centièmes d'unité de base** (1 lot = 100_000 -> 10_000_000).

use std::collections::HashMap;

use opengraph::universe::FX;

use crate::err::ClientError;
use crate::proto::spotware::{ProtoOALightSymbol, ProtoOASymbol};

/// Métadonnées d'un symbole nécessaires au trading.
#[derive(Debug, Clone)]
pub struct SymInfo {
    pub symbol_id: i64,
    /// Chiffres de prix (EURUSD: 5, JPY paires: 3).
    pub digits: i32,
    /// Volume minimum en centièmes d'unité de base (`minVolume`).
    pub min_volume: i64,
    /// Pas de volume en centièmes d'unité (`stepVolume`, >=1).
    pub step_volume: i64,
    pub enabled: bool,
}

impl SymInfo {
    /// int64 venue -> prix f64.
    pub fn to_px(&self, raw: u64) -> f64 {
        raw as f64 / 10f64.powi(self.digits)
    }
    /// prix f64 -> int64 venue (arrondi au digit près).
    pub fn to_raw(&self, px: f64) -> u64 {
        (px * 10f64.powi(self.digits)).round() as u64
    }
    /// Unités de base f64 -> `volume` centièmes d'unité (arrondi au pas).
    /// Retourne 0 si sous le minimum : l'appelant décide alors (abort).
    pub fn to_volume(&self, base_units: f64) -> i64 {
        if !base_units.is_finite() || base_units <= 0.0 {
            return 0;
        }
        let v = (base_units * 100.0).round() as i64;
        let v = v - v.rem_euclid(self.step_volume.max(1));
        if v < self.min_volume {
            0
        } else {
            v
        }
    }
    /// `volume` centièmes -> unités de base f64.
    pub fn from_volume(&self, volume: i64) -> f64 {
        volume as f64 / 100.0
    }
    /// Un pip en unités de prix (pour le slippage guard).
    pub fn pip(&self) -> f64 {
        10f64.powi(1 - self.digits)
    }
}

/// Table sym -> info, dans l'ordre `FX` (0..28).
pub struct SymTable {
    pub info: [Option<SymInfo>; 28],
}

impl SymTable {
    pub fn by_edge(&self, edge: u8) -> Option<&SymInfo> {
        self.info.get(edge as usize)?.as_ref()
    }
    pub fn by_symbol_id(&self, id: i64) -> Option<(u8, &SymInfo)> {
        self.info
            .iter()
            .enumerate()
            .find(|(_, i)| i.as_ref().is_some_and(|i| i.symbol_id == id))
            .map(|(e, i)| (e as u8, i.as_ref().unwrap()))
    }
    /// symbolIds pour `ProtoOASubscribeSpotsReq` / `SymbolByIdReq`.
    pub fn symbol_ids(&self) -> Vec<i64> {
        self.info.iter().flatten().map(|i| i.symbol_id).collect()
    }
}

/// Étape 1 : liste venue (`symbolName`/`symbolId`) -> `symbolId` par paire FX.
/// Retourne la table + la liste des `symbolId` à requêter en SymbolById
/// pour récupérer digits/volumes (`ProtoOALightSymbol` ne les porte pas).
pub fn map_light_symbols(
    list: &[ProtoOALightSymbol<'_>],
) -> Result<(SymTable, Vec<i64>), ClientError> {
    let by_name: HashMap<&str, &ProtoOALightSymbol<'_>> = list
        .iter()
        .filter_map(|s| s.symbolName.as_deref().map(|n| (n, s)))
        .collect();
    let mut info: [Option<SymInfo>; 28] = std::array::from_fn(|_| None);
    let mut ids = Vec::with_capacity(28);
    for (e, &sym) in FX.iter().enumerate() {
        let s = by_name
            .get(sym)
            .ok_or(ClientError::MissingSymbol(FX[e]))?;
        info[e] = Some(SymInfo {
            symbol_id: s.symbolId,
            digits: 0,
            min_volume: 0,
            step_volume: 1,
            enabled: s.enabled.unwrap_or(true),
        });
        ids.push(s.symbolId);
    }
    Ok((SymTable { info }, ids))
}

/// Étape 2 : complète digits/min/step depuis `ProtoOASymbolByIdRes`.
pub fn fill_symbol_details(
    table: &mut SymTable,
    full: &[ProtoOASymbol<'_>],
) -> Result<(), ClientError> {
    for s in full {
        let Some((_, slot)) = table
            .info
            .iter_mut()
            .enumerate()
            .find(|(_, i)| i.as_ref().is_some_and(|i| i.symbol_id == s.symbolId))
        else {
            continue; // symbole hors univers FX
        };
        let si = slot.as_mut().unwrap();
        si.digits = s.digits;
        si.min_volume = s.minVolume.unwrap_or(0);
        si.step_volume = s.stepVolume.unwrap_or(100);
    }
    // chaque symbole requis doit avoir des digits > 0
    for (e, i) in table.info.iter().enumerate() {
        match i {
            Some(i) if i.digits > 0 => {}
            _ => return Err(ClientError::MissingSymbol(FX[e])),
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::borrow::Cow;

    fn light(id: i64, name: &str) -> ProtoOALightSymbol<'static> {
        ProtoOALightSymbol {
            symbolId: id,
            symbolName: Some(Cow::Owned(name.to_string())),
            enabled: Some(true),
            ..Default::default()
        }
    }

    fn full(id: i64, digits: i32) -> ProtoOASymbol<'static> {
        ProtoOASymbol {
            symbolId: id,
            digits,
            minVolume: Some(1000),        // 10 unités
            stepVolume: Some(100),        // 1 unité
            ..Default::default()
        }
    }

    fn table_all() -> SymTable {
        let list: Vec<_> = FX
            .iter()
            .enumerate()
            .map(|(i, s)| light(i as i64 + 1, s))
            .collect();
        let (mut t, ids) = map_light_symbols(&list).unwrap();
        assert_eq!(ids.len(), 28);
        let fulls: Vec<_> = ids
            .iter()
            .map(|&id| full(id, if id % 3 == 0 { 3 } else { 5 }))
            .collect();
        fill_symbol_details(&mut t, &fulls).unwrap();
        t
    }

    #[test]
    fn maps_all_28_pairs() {
        let t = table_all();
        assert_eq!(t.symbol_ids().len(), 28);
        for e in 0..28u8 {
            assert!(t.by_edge(e).is_some());
        }
        let eur = FX.iter().position(|&s| s == "EURUSD").unwrap() as u8;
        assert_eq!(t.by_edge(eur).unwrap().symbol_id, eur as i64 + 1);
    }

    #[test]
    fn missing_pair_is_error() {
        let list: Vec<_> = FX
            .iter()
            .enumerate()
            .filter(|(_, s)| **s != "EURCHF")
            .map(|(i, s)| light(i as i64 + 1, s))
            .collect();
        assert!(matches!(
            map_light_symbols(&list),
            Err(ClientError::MissingSymbol("EURCHF"))
        ));
    }

    #[test]
    fn price_and_volume_conversions() {
        let i5 = SymInfo {
            symbol_id: 1,
            digits: 5,
            min_volume: 1000,
            step_volume: 100,
            enabled: true,
        };
        assert!((i5.to_px(118347) - 1.18347).abs() < 1e-12);
        assert_eq!(i5.to_raw(1.18347), 118347);
        // 1 lot = 100_000 unités -> volume 10_000_000 centièmes
        assert_eq!(i5.to_volume(100_000.0), 10_000_000);
        assert!((i5.from_volume(10_000_000) - 100_000.0).abs() < 1e-9);
        // sous le minimum -> 0
        assert_eq!(i5.to_volume(5.0), 0);
        // pip: 1e-4 à 5 digits
        assert!((i5.pip() - 1e-4).abs() < 1e-12);
        // arrondi au step : 100_055.4 unités -> 10_005_500 (palier de 1 unité)
        assert_eq!(i5.to_volume(100_055.4), 10_005_500);
    }

    #[test]
    fn jpy_digits() {
        let i3 = SymInfo {
            symbol_id: 2,
            digits: 3,
            min_volume: 1000,
            step_volume: 100,
            enabled: true,
        };
        assert!((i3.to_px(150_123) - 150.123).abs() < 1e-12);
        assert!((i3.pip() - 0.01).abs() < 1e-12);
    }
}
