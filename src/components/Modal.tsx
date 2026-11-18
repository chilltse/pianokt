import { X as XMark } from '@/icons'
import { PropsWithChildren, useEffect, useRef } from 'react'
import { Button, ModalOverlay, Modal as RACModal } from 'react-aria-components'
import { twMerge } from 'tailwind-merge'
import { tv } from 'tailwind-variants'
import { Dialog } from './Dialog'

type ModalProps = {
  show: boolean
  onClose: () => void
  className?: string
  modalClassName?: string
  overlayClassName?: string
}

const overlayStyles = tv({
  base: 'fixed inset-0 isolate z-20 flex items-center justify-center overflow-y-auto overflow-x-hidden text-center bg-gray-400/60 min-h-[100dvh] [padding:max(1rem,env(safe-area-inset-top))_max(1rem,env(safe-area-inset-right))_max(1rem,env(safe-area-inset-bottom))_max(1rem,env(safe-area-inset-left))]',
  variants: {
    isEntering: {
      true: 'animate-in fade-in duration-100 ease-out',
    },
    isExiting: {
      true: 'animate-out fade-out duration-100 ease-in',
    },
  },
})

const modalStyles = tv({
  base: 'w-full max-w-md max-h-[min(90dvh,100%)] my-auto rounded-2xl bg-white text-left align-middle text-slate-700 shadow-2xl bg-clip-padding border border-black/10 shrink-0',
  variants: {
    isEntering: {
      true: 'animate-in zoom-in-105 ease-out duration-100',
    },
    isExiting: {
      true: 'animate-out zoom-out-95 ease-in duration-100',
    },
  },
})

export default function Modal({
  show,
  children,
  onClose,
  className,
  modalClassName,
  overlayClassName,
}: PropsWithChildren<ModalProps>) {
  return (
    <ModalOverlay
      className={(renderProps) => overlayStyles({ ...renderProps, className: overlayClassName })}
      isOpen={show}
      isDismissable
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          onClose()
        }
      }}
    >
      <RACModal
        className={(renderProps) => modalStyles({ ...renderProps, className: modalClassName })}
        isDismissable
      >
        <Dialog className={twMerge('relative rounded-md bg-white', className)} aria-label="Modal">
          <Button
            className="absolute top-6 right-5 z-10 cursor-pointer text-gray-400 hover:text-gray-600 active:text-gray-700"
            onPress={onClose}
          >
            <XMark height={24} width={24} />
          </Button>
          {children}
        </Dialog>
      </RACModal>
    </ModalOverlay>
  )
}
