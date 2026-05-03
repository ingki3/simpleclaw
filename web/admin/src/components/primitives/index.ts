/**
 * primitives — Modal/Drawer/Toast/ConfirmGate/RestartStepper.
 *
 * 영역별 화면은 ``import { Modal, Drawer, ... } from "@/components/primitives"``
 * 형태로 가져온다. 전역 ToastProvider는 RootLayout에서 단 1회 마운트된다.
 */

export { Modal, type ModalProps, type ModalSize } from "./Modal";
export { Drawer, type DrawerProps, type DrawerSize } from "./Drawer";
export {
  ToastProvider,
  useToast,
  type ToastInput,
  type ToastTone,
  type ToastUndoAction,
} from "./Toast";
export { ConfirmGate, type ConfirmGateProps } from "./ConfirmGate";
export {
  RestartStepper,
  type RestartStep,
  type RestartStepperProps,
} from "./RestartStepper";
