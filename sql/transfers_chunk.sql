-- Per-wallet-per-day Phantom fee activity for a date chunk.
-- Save this in Dune with {{start_date}} and {{end_date}} as Text parameters.
-- Roster = wallets that paid a fee to one of Phantom's 8 Solana fee wallets;
-- from_owner is the paying user (validated ~28.8k distinct/day).
SELECT from_owner                    AS wallet,
       date_trunc('day', block_time) AS activity_day,
       count(*)                      AS fee_swaps,   -- activity (complete)
       sum(amount_usd)               AS fee_usd      -- ~90% priced; volume ~ fee_usd / 0.0085
FROM tokens_solana.transfers
WHERE to_owner IN (
  '25mYnjJ2MXHZH6NvTTdA63JvjgRVcuiaj6MRiEQNs1Dq',
  '9yj3zvLS3fDMqi1F8zhkaWfq8TZpZWHe6cz1Sgt7djXf',
  '8psNvWTrdNTiVRNzAgsou9kETXNJm2SXZyaKuJraVRtf',
  'CnmA6Zb8hLrG33AT4RTzKdGv1vKwRBKQQr8iNckvv8Yg',
  '2rQZb9xqQGwoCMDkpabbzDB9wyPTjSPj9WNhJodTaRHm',
  '9gnLg6NtVxaASvxtADLFKZ9s8yHft1jXb1Vu6gVKvh1J',
  'wtpXRqKLdGc7vpReogsRugv6EFCw4HBHcxm8pFcR84a',
  'D1NJy3Qq3RKBG29EDRj28ozbGwnhmM5yBUp8PonSYUnm'
)
  AND abs(from_big_endian_64(xxhash64(to_utf8(from_owner)))) % 60 = 0   -- ~1/60 uniform random sample (hash of wallet; tune the 60)
  AND block_time >= TIMESTAMP '{{start_date}}'
  AND block_time <  TIMESTAMP '{{end_date}}'
GROUP BY 1, 2
