"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  table: ({ children }) => (
    <div className="audit-table-wrapper">
      <table className="audit-table">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="audit-th">{children}</th>,
  td: ({ children }) => <td className="audit-td">{children}</td>,
  code: ({ children }) => <code className="audit-code">{children}</code>,
  strong: ({ children }) => <strong className="audit-strong">{children}</strong>,
};

export default function MarkdownResponse({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {content}
    </ReactMarkdown>
  );
}