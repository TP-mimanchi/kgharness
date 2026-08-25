import { memo } from "react";
import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownRendererProps {
  content: string;
  streaming?: boolean;
}

const REMARK_PLUGINS = [remarkGfm];
const MARKDOWN_COMPONENTS = {
  a({ children, href, ...props }: ComponentPropsWithoutRef<"a">) {
    return (
      <a href={href} rel="noreferrer" target="_blank" {...props}>
        {children}
      </a>
    );
  }
};

const ParsedMarkdown = memo(function ParsedMarkdown({ content }: { content: string }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        components={MARKDOWN_COMPONENTS}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  streaming = false,
}: MarkdownRendererProps) {
  if (streaming) {
    return (
      <div className="streaming-markdown streaming-markdown--active">
        <div className="markdown-body markdown-body--streaming">{content}</div>
      </div>
    );
  }
  return (
    <div className="streaming-markdown">
      <ParsedMarkdown content={content} />
    </div>
  );
});
