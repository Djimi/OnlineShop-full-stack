import type { ItemDTO } from '../../types/api';
import { Card } from '../common/Card';
import { Button } from '../common/Button';
import { ChevronRight, AlertCircle } from 'lucide-react';
import { useNavigate } from 'react-router';

interface ItemCardProps {
  item: ItemDTO;
}

export function ItemCard({ item }: ItemCardProps) {
  const navigate = useNavigate();
  const isOutOfStock = item.quantity <= 0;

  const handleViewDetails = () => {
    navigate(`/items/${item.id}`);
  };

  return (
    <Card className="flex flex-col h-full">
      <div className="flex-1">
        {/* Item Header */}
        <div className="mb-3">
          <h3 className="font-display text-3xl font-normal leading-none line-clamp-2">
            {item.name}
          </h3>
        </div>

        {/* Stock Status */}
        <div className="mb-3">
          {isOutOfStock ? (
            <div className="flex items-center gap-2 text-accent border-y border-hair px-1 py-2">
              <AlertCircle aria-hidden="true" className="w-4 h-4" />
              <span className="text-sm font-medium">Out of Stock</span>
            </div>
          ) : (
            <div className="text-sm text-soft font-light">
              <span>Available: </span>
              <span className="font-medium text-ink">{item.quantity} units</span>
            </div>
          )}
        </div>

        {/* Description */}
        <p className="text-soft text-sm font-light leading-relaxed line-clamp-2 mb-4">
          {item.description}
        </p>
      </div>

      {/* Button */}
      <Button
        variant={isOutOfStock ? 'secondary' : 'primary'}
        size="sm"
        fullWidth
        onClick={handleViewDetails}
        className="flex items-center justify-center gap-2"
      >
        <span>View Details</span>
        <ChevronRight className="w-4 h-4" />
      </Button>
    </Card>
  );
}