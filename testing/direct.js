import http from 'k6/http';
import { check } from 'k6';

export const options = {
    duration: '10s',
};

// this was for checking Static via Nginx
export default function () {
     const res = http.get("http://54.167.105.59/static/images/NoteTube-logo.png?v=3");
     check(res, { '200': (r) => r.status === 200});
 };