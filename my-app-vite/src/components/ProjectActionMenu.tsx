import { useEffect, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';

export type ProjectActionMenuItem = {
  key: string;
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  danger?: boolean;
  title?: string;
};

type ProjectActionMenuProps = {
  items: ProjectActionMenuItem[];
  onClose: () => void;
  anchorRect: DOMRect;
};

function ProjectActionMenu({ items, onClose, anchorRect }: ProjectActionMenuProps) {
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onClose();
      }
    }

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const menuStyle = useMemo(() => {
    const menuWidth = 260;
    const gap = 8;
    const minPadding = 8;
    const maxLeft = window.innerWidth - menuWidth - minPadding;
    const preferredLeft = anchorRect.right + gap;
    const left = Math.max(minPadding, Math.min(preferredLeft, maxLeft));
    const top = Math.max(minPadding, anchorRect.top);

    return {
      top,
      left,
      width: menuWidth,
    };
  }, [anchorRect]);

  return createPortal(
    <>
      <div
        className="project-action-menu-backdrop"
        onMouseDown={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }}
        onTouchStart={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }}
      />
      <div
        ref={menuRef}
        className="project-action-menu"
        role="menu"
        style={{ top: menuStyle.top, left: menuStyle.left, width: menuStyle.width }}
        onMouseDown={(event) => event.stopPropagation()}
        onClick={(event) => event.stopPropagation()}
      >
        {items.map((item) => (
          <button
            key={item.key}
            type="button"
            role="menuitem"
            className={`btn btn--ghost project-action-menu__item${item.danger ? ' project-action-menu__item--danger' : ''}`}
            disabled={item.disabled}
            title={item.title}
            onClick={() => {
              if (item.disabled) return;
              item.onSelect();
              onClose();
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
    </>,
    document.body,
  );
}

export default ProjectActionMenu;
