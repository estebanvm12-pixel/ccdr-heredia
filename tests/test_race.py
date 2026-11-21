"""Acceptance criterion: two admins approving overlapping requests at the same time.

Runs against the real ThreadingHTTPServer and SQLite file, many rounds, with a
barrier so both approvals hit the server at the same instant.
"""
import threading

from tests.helpers import ServerTestCase

ROUNDS = 12


class RaceTests(ServerTestCase):
    def test_concurrent_approvals_never_double_book(self):
        tab_a, tab_b = self.admin(), self.admin()  # two separate sessions ("two tabs")
        for round_no in range(ROUNDS):
            offset = 3 + round_no
            first, _ = self.create_request(offset=offset, start="10:00", end="12:00")
            second, _ = self.create_request(offset=offset, start="11:00", end="13:00")
            barrier = threading.Barrier(2)
            results = {}

            def approve(client, request_id):
                barrier.wait()
                status, _, _ = client.request("POST", f"/api/admin/requests/{request_id}/approve", {})
                results[request_id] = status

            threads = [threading.Thread(target=approve, args=(tab_a, first)),
                       threading.Thread(target=approve, args=(tab_b, second))]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            self.assertEqual(sorted(results.values()), [200, 409], f"round {round_no}: {results}")

        conn = self.db()
        clashes = conn.execute(
            "SELECT COUNT(*) FROM booking_requests a JOIN booking_requests b "
            "ON a.facility_id = b.facility_id AND a.id < b.id "
            "AND a.status = 'approved' AND b.status = 'approved' "
            "AND a.starts_at < b.ends_at AND b.starts_at < a.ends_at").fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM booking_requests WHERE status = 'approved'").fetchone()[0]
        conn.close()
        self.assertEqual(clashes, 0)
        self.assertEqual(approved, ROUNDS)

    def test_same_request_approved_twice_concurrently(self):
        tab_a, tab_b = self.admin(), self.admin()
        rid, _ = self.create_request(offset=40)
        barrier = threading.Barrier(2)
        codes = []

        def approve(client):
            barrier.wait()
            codes.append(client.request("POST", f"/api/admin/requests/{rid}/approve", {})[0])

        threads = [threading.Thread(target=approve, args=(c,)) for c in (tab_a, tab_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(sorted(codes), [200, 409])
