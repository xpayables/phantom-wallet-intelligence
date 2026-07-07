-- Per-wallet-per-day Phantom fee activity for a date chunk.
-- Save in Dune with {{start_date}}, {{end_date}}, and {{fee_wallets}} as Text parameters.
-- {{fee_wallets}} is a comma-separated list of Phantom's Solana fee wallets (DefiLlama fees/phantom.ts),
-- passed from config.FEE_WALLETS so the roster isn't hardcoded here. from_owner is the paying user.
SELECT from_owner                    AS wallet,
       date_trunc('day', block_time) AS activity_day,
       count(*)                      AS fee_swaps,   -- activity (complete)
       sum(amount_usd)               AS fee_usd      -- ~90% priced; volume ~ fee_usd / 0.0085
FROM tokens_solana.transfers
WHERE contains(split('{{fee_wallets}}', ','), to_owner)                  -- fee wallets from the param, not hardcoded
  AND abs(from_big_endian_64(xxhash64(to_utf8(from_owner)))) % 60 = 0   -- ~1/60 uniform random sample (hash of wallet; tune the 60)
  AND block_time >= TIMESTAMP '{{start_date}}'
  AND block_time <  TIMESTAMP '{{end_date}}'
GROUP BY 1, 2
