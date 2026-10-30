import ReactMarkdown from "react-markdown";

// A reply is model output built from stored text, so it is untrusted. It is rendered as a fixed set
// of text elements only: never raw HTML (react-markdown shows it as text), never images (an image
// URL can carry data out to another site), and links only for http and https.
const ALLOWED = [
  "p", "strong", "em", "del", "br", "hr", "blockquote",
  "ul", "ol", "li", "code", "pre", "a",
  "h1", "h2", "h3", "h4", "h5", "h6",
  "table", "thead", "tbody", "tr", "th", "td",
];

const webUrl = (url: string) => (/^https?:\/\//i.test(url) ? url : "");

export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none break-words">
      <ReactMarkdown
        allowedElements={ALLOWED}
        unwrapDisallowed
        urlTransform={webUrl}
        components={{
          a: ({ href, children }) =>
            href ? (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <>{children}</>
            ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
