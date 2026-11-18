import Modal from '@/components/Modal'
import ReactMarkdown from 'react-markdown'

type ConsentModalProps = {
  open: boolean
  content: string
  onClose: () => void
  onAgree: () => void
}

export default function ConsentModal({
  open,
  content,
  onClose,
  onAgree,
}: ConsentModalProps) {
  return (
    <Modal show={open} onClose={onClose} modalClassName="max-w-2xl">
      <div className="flex max-h-[80vh] flex-col px-6 pb-6 pt-8">
        <div className="prose prose-sm prose-stone max-h-[60vh] overflow-y-auto pr-2">
          <ReactMarkdown
            components={{
              h1: ({ children }) => (
                <h1 className="mb-4 mt-2 text-xl font-semibold text-gray-900">
                  {children}
                </h1>
              ),
              h2: ({ children }) => (
                <h2 className="mb-3 mt-4 text-base font-semibold text-gray-800">
                  {children}
                </h2>
              ),
              p: ({ children }) => (
                <p className="mb-2 text-sm leading-relaxed text-gray-700">
                  {children}
                </p>
              ),
              ul: ({ children }) => (
                <ul className="mb-3 ml-4 list-disc space-y-1 text-sm text-gray-700">
                  {children}
                </ul>
              ),
              li: ({ children }) => (
                <li className="leading-relaxed">{children}</li>
              ),
              strong: ({ children }) => (
                <strong className="font-semibold text-gray-900">{children}</strong>
              ),
              hr: () => <hr className="my-4 border-gray-200" />,
            }}
          >
            {content}
          </ReactMarkdown>
        </div>
        <div className="mt-6 flex shrink-0 justify-end gap-3 border-t border-gray-100 pt-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            I do not agree
          </button>
          <button
            type="button"
            onClick={onAgree}
            className="rounded-md bg-stone-600 px-4 py-2 text-sm font-medium text-white hover:bg-stone-700"
          >
            I agree
          </button>
        </div>
      </div>
    </Modal>
  )
}
