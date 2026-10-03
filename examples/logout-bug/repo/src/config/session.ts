import session from "express-session";
import RedisStore from "connect-redis";
import { redis } from "../lib/redis";

export const SESSION_TTL_SECONDS = 24 * 60 * 60; // sessions should last 24 hours

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
    secure: true,
    httpOnly: true,
    sameSite: "lax",
  },
});
