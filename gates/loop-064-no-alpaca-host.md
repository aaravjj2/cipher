# Gates: loop 064 no Alpaca host in web src

Scope: browser source does not hardcode api.alpaca.markets.

- [x] G1: Host walk test passes.
  CHECK: node --test cipher-system/web/test/no-alpaca-host.test.mjs
  EXPECT: /fail 0/

- [x] G2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/
