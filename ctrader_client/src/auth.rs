//! Authentification 2 étages + refresh token + garde compte NETTED.
//!
//! ```text
//! 2100 app auth (clientId/secret) -> 2101
//! 2102 account auth (accessToken/ctid) -> 2103
//! 2121 trader info -> 2122 (accountType : NETTED requis)
//! 2149 account list (accessToken) -> 2150 (résolution du ctid)
//! 2173 refresh token -> 2174 (rotation ~14 j)
//! ```

use std::borrow::Cow;

use crate::err::ClientError;
use crate::proto::spotware::{
    ProtoOAAccountAuthReq, ProtoOAAccountType, ProtoOAApplicationAuthReq,
    ProtoOAGetAccountListByAccessTokenReq, ProtoOAGetAccountListByAccessTokenRes,
    ProtoOAPayloadType, ProtoOARefreshTokenReq, ProtoOARefreshTokenRes, ProtoOATraderReq,
    ProtoOATraderRes,
};

/// Étape 1 : application `clientId`/`clientSecret` (2100).
pub fn app_auth_req<'a>(client_id: &'a str, client_secret: &'a str) -> ProtoOAApplicationAuthReq<'a> {
    ProtoOAApplicationAuthReq {
        payloadType: Some(ProtoOAPayloadType::PROTO_OA_APPLICATION_AUTH_REQ),
        clientId: Cow::Borrowed(client_id),
        clientSecret: Cow::Borrowed(client_secret),
    }
}

/// Étape 2 : compte `accessToken` + `ctidTraderAccountId` (2102).
pub fn account_auth_req<'a>(access_token: &'a str, ctid: i64) -> ProtoOAAccountAuthReq<'a> {
    ProtoOAAccountAuthReq {
        payloadType: Some(ProtoOAPayloadType::PROTO_OA_ACCOUNT_AUTH_REQ),
        accessToken: Cow::Borrowed(access_token),
        ctidTraderAccountId: ctid,
    }
}

/// `ProtoOATraderReq` (2121) — métadonnées du compte (garde NETTED).
pub fn trader_req(ctid: i64) -> ProtoOATraderReq {
    ProtoOATraderReq {
        payloadType: Some(ProtoOAPayloadType::PROTO_OA_TRADER_REQ),
        ctidTraderAccountId: ctid,
    }
}

/// `ProtoOAGetAccountListByAccessTokenReq` (2149) — liste les comptes
/// couverts par le token quand `ctidTraderAccountId` n'est pas configuré.
pub fn account_list_req<'a>(access_token: &'a str) -> ProtoOAGetAccountListByAccessTokenReq<'a> {
    ProtoOAGetAccountListByAccessTokenReq {
        payloadType: Some(ProtoOAPayloadType::PROTO_OA_GET_ACCOUNTS_BY_ACCESS_TOKEN_REQ),
        accessToken: Cow::Borrowed(access_token),
    }
}

/// `ProtoOARefreshTokenReq` (2173).
pub fn refresh_req<'a>(refresh_token: &'a str) -> ProtoOARefreshTokenReq<'a> {
    ProtoOARefreshTokenReq {
        payloadType: Some(ProtoOAPayloadType::PROTO_OA_REFRESH_TOKEN_REQ),
        refreshToken: Cow::Borrowed(refresh_token),
    }
}

/// Garde-fou NETTED — refuse de trader sur un compte HEDGED/SPREAD_BETTING
/// (notre modèle = soldes de devises, pas de `positionId` individuels).
pub fn check_netted(res: &ProtoOATraderRes<'_>) -> Result<(), ClientError> {
    match res.trader.accountType {
        Some(ProtoOAAccountType::NETTED) => Ok(()),
        Some(t) => Err(ClientError::NotNetted(format!("{t:?}"))),
        None => Err(ClientError::Inconsistent(
            "ProtoOATraderRes sans accountType".into(),
        )),
    }
}

/// Paire `(access, refresh)` renouvelée — à persister par l'appelant.
pub fn token_pair(res: &ProtoOARefreshTokenRes<'_>) -> (String, String, i64) {
    (
        res.accessToken.to_string(),
        res.refreshToken.to_string(),
        res.expiresIn,
    )
}

/// `ctidTraderAccountId` le mieux adapté : `isLive` filtré sur l'env
/// (démo -> `false`, live -> `true`). Plusieurs comptes démo possibles —
/// on prend le premier.
pub fn pick_account(
    res: &ProtoOAGetAccountListByAccessTokenRes<'_>,
    live: bool,
) -> Option<i64> {
    res.ctidTraderAccount
        .iter()
        .find(|a| a.isLive.unwrap_or(false) == live)
        .map(|a| a.ctidTraderAccountId)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proto::spotware::{ProtoOACtidTraderAccount, ProtoOATrader};

    fn trader_res(t: Option<ProtoOAAccountType>) -> ProtoOATraderRes<'static> {
        ProtoOATraderRes {
            payloadType: Some(ProtoOAPayloadType::PROTO_OA_TRADER_RES),
            ctidTraderAccountId: 1,
            trader: ProtoOATrader {
                accountType: t,
                ..Default::default()
            },
        }
    }

    #[test]
    fn netted_passes_hedged_refused() {
        assert!(check_netted(&trader_res(Some(ProtoOAAccountType::NETTED))).is_ok());
        let e = check_netted(&trader_res(Some(ProtoOAAccountType::HEDGED))).unwrap_err();
        assert!(matches!(e, ClientError::NotNetted(_)));
        // absence du champ -> incohérent, pas autorisé
        assert!(check_netted(&trader_res(None)).is_err());
    }

    #[test]
    fn picks_right_account() {
        let res = ProtoOAGetAccountListByAccessTokenRes {
            payloadType: None,
            accessToken: Cow::Borrowed("t"),
            permissionScope: None,
            ctidTraderAccount: vec![
                ProtoOACtidTraderAccount {
                    ctidTraderAccountId: 111,
                    isLive: Some(false),
                    ..Default::default()
                },
                ProtoOACtidTraderAccount {
                    ctidTraderAccountId: 222,
                    isLive: Some(true),
                    ..Default::default()
                },
            ],
        };
        assert_eq!(pick_account(&res, false), Some(111));
        assert_eq!(pick_account(&res, true), Some(222));
    }

    #[test]
    fn request_builders_carry_payload_type() {
        let a = app_auth_req("id", "sec");
        assert_eq!(a.payloadType, Some(ProtoOAPayloadType::PROTO_OA_APPLICATION_AUTH_REQ));
        let b = account_auth_req("tok", 7);
        assert_eq!(b.ctidTraderAccountId, 7);
        let t = trader_req(7);
        assert_eq!(t.payloadType, Some(ProtoOAPayloadType::PROTO_OA_TRADER_REQ));
        let r = refresh_req("rt");
        assert_eq!(r.refreshToken, "rt");
    }
}
