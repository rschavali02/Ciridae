import { Link } from "react-router";

/**
 * Reached when a URL does not match any route -- most likely a shared invoice
 * link that got mangled in transit. Since shareable URLs are the point of
 * routing here, a dead one has to offer a way back rather than a stack trace.
 */
function NotFound() {
  return (
    <main className="home-idle">
      <h1>Page not found</h1>
      <p>That address does not match anything in the review queue.</p>
      <Link className="back-link" to="/">← Back to queue</Link>
    </main>
  );
}

export default NotFound;
