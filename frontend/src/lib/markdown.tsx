"use client"

import React from "react"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import rehypeHighlight from "rehype-highlight"

/** Replace [N] citation markers in text with styled <sup> badges */
function processCitations(children: React.ReactNode): React.ReactNode {
  return React.Children.map(children, (child) => {
    if (typeof child !== "string") return child
    const parts = child.split(/(\[\d+\])/)
    if (parts.length === 1) return child
    return parts.map((part, i) => {
      const m = part.match(/^\[(\d+)\]$/)
      if (m) {
        return (
          <sup
            key={i}
            className="inline-flex items-center justify-center min-w-[1.1em] h-[1.1em] px-0.5 ml-0.5 rounded text-[0.65em] font-medium bg-primary/10 text-primary align-super cursor-default"
          >
            {m[1]}
          </sup>
        )
      }
      return part
    })
  })
}

interface MarkdownContentProps {
  content: string
  className?: string
}

export function MarkdownContent({ content, className }: MarkdownContentProps) {
  return (
    <div className={`min-w-0 overflow-hidden ${className ?? ""}`}>
      <Markdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, rehypeHighlight]}
        components={{
          pre({ children, ...props }) {
            return (
              <pre
                className="overflow-x-auto rounded-lg bg-muted/50 p-4 text-sm font-mono my-3 max-w-full"
                {...props}
              >
                {children}
              </pre>
            )
          },
          code({ children, className: codeClassName, ...props }) {
            const isInline = !codeClassName
            if (isInline) {
              return (
                <code
                  className="rounded-md bg-muted/60 px-1.5 py-0.5 text-[0.9em] font-mono"
                  {...props}
                >
                  {children}
                </code>
              )
            }
            return (
              <code className={codeClassName} {...props}>
                {children}
              </code>
            )
          },
          p({ children, ...props }) {
            return (
              <p className="mb-3 last:mb-0 leading-relaxed" {...props}>
                {processCitations(children)}
              </p>
            )
          },
          ul({ children, ...props }) {
            return (
              <ul className="mb-3 list-disc pl-6 last:mb-0 space-y-1" {...props}>
                {children}
              </ul>
            )
          },
          ol({ children, ...props }) {
            return (
              <ol className="mb-3 list-decimal pl-6 last:mb-0 space-y-1" {...props}>
                {children}
              </ol>
            )
          },
          li({ children, ...props }) {
            return (
              <li className="leading-relaxed" {...props}>
                {processCitations(children)}
              </li>
            )
          },
          h1({ children, ...props }) {
            return (
              <h1 className="mt-6 mb-3 text-xl font-bold first:mt-0" {...props}>
                {children}
              </h1>
            )
          },
          h2({ children, ...props }) {
            return (
              <h2 className="mt-5 mb-2 text-lg font-semibold first:mt-0" {...props}>
                {children}
              </h2>
            )
          },
          h3({ children, ...props }) {
            return (
              <h3 className="mt-4 mb-2 text-base font-semibold first:mt-0" {...props}>
                {children}
              </h3>
            )
          },
          table({ children, ...props }) {
            return (
              <div className="my-3 overflow-x-auto rounded-lg border border-border">
                <table
                  className="w-full border-collapse text-sm"
                  {...props}
                >
                  {children}
                </table>
              </div>
            )
          },
          thead({ children, ...props }) {
            return (
              <thead className="bg-muted/40" {...props}>
                {children}
              </thead>
            )
          },
          th({ children, ...props }) {
            return (
              <th
                className="border-b border-border px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground"
                {...props}
              >
                {children}
              </th>
            )
          },
          td({ children, ...props }) {
            return (
              <td className="border-b border-border/50 px-3 py-2" {...props}>
                {processCitations(children)}
              </td>
            )
          },
          blockquote({ children, ...props }) {
            return (
              <blockquote
                className="my-3 border-l-2 border-primary/40 pl-4 italic text-muted-foreground"
                {...props}
              >
                {children}
              </blockquote>
            )
          },
          hr(props) {
            return <hr className="my-4 border-border" {...props} />
          },
          a({ children, ...props }) {
            return (
              <a target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2 hover:text-primary/80" {...props}>
                {children}
              </a>
            )
          },
          strong({ children, ...props }) {
            return (
              <strong className="font-semibold text-foreground" {...props}>
                {children}
              </strong>
            )
          },
        }}
      >
        {content}
      </Markdown>
    </div>
  )
}
