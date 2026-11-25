/* Markdown from the AI (reports, advice, answers): GitHub-flavoured, so tables, task lists
   and strikethrough render; wide tables scroll sideways instead of breaking the card. */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{ table: ({ node: _node, ...props }) => <div className="md-table"><table {...props} /></div> }}
    >
      {children}
    </ReactMarkdown>
  );
}
