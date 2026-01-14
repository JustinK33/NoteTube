import http from 'k6/http';
import { check } from 'k6';

export const options = {
    duration: '10s',
};

// testing straight towards django
export default function () {
    const res = http.get("http://web:8000/static/images/NoteTube-logo.png", {
        headers: { Host: '54.167.105.59'}
    });
    check(res, { '200': (r) => r.status === 200});
}