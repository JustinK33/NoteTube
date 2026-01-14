import http from "k6/http";
import { check } from "k6";

export const options = { vus: 1, iterations: 200 };

export default function () {
  const res = http.get("http://127.0.0.1:8000/static/images/NoteTube-logo.png");
  check(res, { "status is 200": (r) => r.status === 200 });
}
