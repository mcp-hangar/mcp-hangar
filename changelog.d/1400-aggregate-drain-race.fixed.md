**core:** an aggregate no longer drops a domain event that another thread
records while its events are being collected. `collect_events()` copied the
pending events and then cleared the list, so an event appended in between was
cleared without being returned, and nothing published it: no metric, saga,
audit record or event store entry. The health worker and a recovery saga's
scheduled restart do exactly that to the same server. In a stress test that
recorded 20,000 events on one thread while another drained, the old code lost
between 1,797 and 6,383 of them per run. Events are now drained one at a time,
so none is lost.
