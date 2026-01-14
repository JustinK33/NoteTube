import http from "k6/http";
import { check } from "k6";

export const options = { duration: "10s", vus: 1 };

export default function () {
  const res = http.get("http://web:8000/static/images/NoteTube-logo.png");

  const ok = check(res, { "status is 200": (r) => r.status === 200 });

  if (!ok) {
    console.log(`status=${res.status}`);
    console.log(`body=${res.body.slice(0, 300)}`);
  }
}