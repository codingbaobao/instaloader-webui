import { useEffect, useRef, useState } from "react";

type ConfirmDialogProps = Readonly<{
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  onClose: () => void;
  onConfirm: () => Promise<void> | void;
}>;

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  onClose,
  onConfirm,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog === null) {
      return;
    }
    if (open && !dialog.open) {
      dialog.showModal();
      cancelRef.current?.focus();
    }
    if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  async function confirm() {
    setConfirming(true);
    try {
      await onConfirm();
    } finally {
      setConfirming(false);
    }
  }

  return (
    <dialog
      aria-labelledby="confirm-dialog-title"
      className="confirm-dialog"
      ref={dialogRef}
      onCancel={(event) => {
        event.preventDefault();
        if (!confirming) {
          onClose();
        }
      }}
    >
      <div className="confirm-dialog-body">
        <p className="eyebrow">Confirmation required</p>
        <h2 id="confirm-dialog-title">{title}</h2>
        <p>{description}</p>
        <div className="dialog-actions">
          <button
            className="secondary-button"
            ref={cancelRef}
            type="button"
            disabled={confirming}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="danger-button"
            type="button"
            disabled={confirming}
            onClick={() => void confirm()}
          >
            {confirming ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
