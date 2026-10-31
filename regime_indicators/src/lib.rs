//! Indicateurs de régime pour le moteur opengraph — chemins ouverts FX.
//!
//! Deux indicateurs en forme close (conception v1.0, inspirés des équations
//! 3 et 4 d'OpenGraph, Xia-Kao-Huang 2024) :
//! - T̃_t : tension multi-sauts (composante cyclique de Hodge, propagation
//!   Ã = Ā + Ā² + Ā³, dé-normalisée) ;
//! - D_t : dominance spectrale σ₁²/Σσₖ² des rendements panier des 8 devises.
//!
//! Intégration : porte dure à hystérésis (fail-closed) et modulation
//! monotone `gπ` (réutilisée pour le seuil adaptatif ddd_min_t).
//! Calcul hors chemin chaud ; publication par instantané immuable.

pub mod tension;
pub mod svd_regime;
pub mod smoothing;
pub mod gate;
pub mod snapshot;
pub mod engine;

pub use snapshot::RegimeSnapshot;
