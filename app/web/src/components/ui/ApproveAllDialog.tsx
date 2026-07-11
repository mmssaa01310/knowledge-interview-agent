type ApproveAllDialogProps = {
  message: string;
  onConfirm: () => void;
};

export function confirmApproveAll({ message, onConfirm }: ApproveAllDialogProps) {
  if (window.confirm(message)) {
    onConfirm();
  }
}
