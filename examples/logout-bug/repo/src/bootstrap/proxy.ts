import type { Express } from "express";

// We run behind the ingress load balancer, which terminates TLS.
export function configureProxy(app: Express) {
  app.set("trust proxy", 1);
}
