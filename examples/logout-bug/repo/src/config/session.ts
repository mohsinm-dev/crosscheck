import session from "express-session";
import RedisStore from "connect-redis";
import { redis } from "../lib/redis";

const DAY_MS = 24 * 60 * 60 * 1000;

export const sessionMiddleware = session({
  store: new RedisStore({
    client: redis,
    prefix: "sess:",
    ttl: 600, // seconds
  }),
  secret: process.env.SESSION_SECRET!,
  resave: false,
  rolling: false,
  saveUninitialized: false,
  cookie: {
    maxAge: DAY_MS,
    secure: true,
    httpOnly: true,
    sameSite: "lax",
  },
});
