import http from 'k6/http';
import { check } from 'k6';

export const options = { duration: '10s', vus: 1 };

export default function () {
  // IMPORTANT: hit a Django endpoint, not /static
  const res = http.get('http://http://localhost/static/images/NoteTube-logo.png', {
    // IMPORTANT: don't override Host unless you really need to
    // headers: { Host: '54.167.105.59' },
  });

  check(res, { 'status is 200': (r) => r.status === 200 });

  if (res.status !== 200) {
    console.log(`status=${res.status}`);
    console.log(`body=${res.body && res.body.slice ? res.body.slice(0, 200) : res.body}`);
  }
}
