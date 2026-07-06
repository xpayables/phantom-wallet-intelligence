-- Idle stablecoin balance (USD) at cutoff T for the sampled wallets: USDC+USDT summed per wallet.
-- Same ~1/60 hash sample as transfers_chunk.sql, so this returns a slice, not the full ~8.5M-holder
-- snapshot; joined to the roster locally. Save in Dune with {{balance_day}} as a Text parameter.
SELECT address         AS wallet,
       SUM(balance_usd) AS stablecoin_usd
FROM stablecoins_solana.balances
WHERE day = TIMESTAMP '{{balance_day}}'
  AND token_symbol IN ('USDC', 'USDT')
  AND abs(from_big_endian_64(xxhash64(to_utf8(address)))) % 60 = 0   -- same ~1/60 hash sample as transfers; join to roster locally
GROUP BY 1
