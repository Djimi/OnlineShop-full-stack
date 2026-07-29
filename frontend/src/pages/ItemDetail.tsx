import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router';
import type { ItemDTO } from '../types/api';
import { itemsService } from '../services/itemsService';
import { Button } from '../components/common/Button';
import { ArrowLeft, AlertCircle, CheckCircle, Package } from 'lucide-react';
import toast from 'react-hot-toast';

export default function ItemDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [item, setItem] = useState<ItemDTO | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchItem = async () => {
      if (!id) {
        setError('Item ID not found');
        setIsLoading(false);
        return;
      }

      try {
        setIsLoading(true);
        setError(null);
        const data = await itemsService.getItemById(id);
        setItem(data);
      } catch (error: any) {
        const errorMessage =
          error.response?.status === 404
            ? 'Item not found'
            : error.response?.data?.detail ||
              error.message ||
              'Failed to load item details';
        setError(errorMessage);
        toast.error(errorMessage);
        console.error('Error fetching item:', error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchItem();

    return () => {
      toast.dismiss();
    };
  }, [id]);

  const isOutOfStock = item && item.quantity === 0;

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        {/* Back Button */}
        <button
          onClick={() => navigate('/items')}
          className="flex items-center gap-2 text-blue-600 hover:text-blue-700 font-medium mb-8 transition-colors"
        >
          <ArrowLeft className="w-5 h-5" />
          <span>Back to Catalog</span>
        </button>

        {/* Breadcrumbs */}
        <div className="text-sm text-gray-600 mb-8">
          <a href="/items" className="hover:text-gray-900">Catalog</a>
          <span className="mx-2">/</span>
          <span className="text-gray-900">
            {item?.name || 'Loading...'}
          </span>
        </div>

        {/* Loading State */}
        {isLoading && (
          <div className="bg-white rounded-lg shadow-md p-12 text-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4"></div>
            <p className="text-gray-600">Loading item details...</p>
          </div>
        )}

        {/* Error State */}
        {error && !isLoading && (
          <div className="bg-white rounded-lg shadow-md p-8 text-center">
            <AlertCircle className="w-12 h-12 text-red-600 mx-auto mb-4" />
            <h2 className="text-2xl font-bold text-gray-900 mb-2">
              Oops! Something went wrong
            </h2>
            <p className="text-gray-600 mb-6">{error}</p>
            <Button
              variant="primary"
              onClick={() => navigate('/items')}
            >
              Return to Catalog
            </Button>
          </div>
        )}

        {/* Item Details */}
        {item && !isLoading && (
          <div className="bg-white rounded-lg shadow-lg overflow-hidden">
            <div className="grid md:grid-cols-2 gap-8 p-8">
              {/* Left Column - Product Image Placeholder */}
              <div className="flex items-center justify-center">
                <div className="w-full aspect-square bg-gradient-to-br from-blue-100 to-blue-50 rounded-lg flex items-center justify-center">
                  <Package className="w-32 h-32 text-blue-300" />
                </div>
              </div>

              {/* Right Column - Details */}
              <div className="flex flex-col justify-between">
                {/* Product Info */}
                <div>
                  <h1 className="text-4xl font-bold text-gray-900 mb-4">
                    {item.name}
                  </h1>

                  {/* Stock Status */}
                  <div className="mb-6">
                    {isOutOfStock ? (
                      <div className="flex items-center gap-2 text-red-600 bg-red-50 px-4 py-3 rounded-lg">
                        <AlertCircle className="w-5 h-5" />
                        <span className="font-semibold">Out of Stock</span>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 text-green-600 bg-green-50 px-4 py-3 rounded-lg">
                        <CheckCircle className="w-5 h-5" />
                        <span className="font-semibold">
                          In Stock ({item.quantity} available)
                        </span>
                      </div>
                    )}
                  </div>

                  {/* Description */}
                  <div className="mb-8">
                    <h2 className="text-lg font-semibold text-gray-900 mb-2">
                      Description
                    </h2>
                    <p className="text-gray-700 leading-relaxed">
                      {item.description}
                    </p>
                  </div>

                  {/* Specifications */}
                  <div className="bg-gray-50 rounded-lg p-4 mb-8">
                    <h2 className="text-lg font-semibold text-gray-900 mb-4">
                      Specifications
                    </h2>
                    <div className="space-y-3">
                      <div className="flex justify-between">
                        <span className="text-gray-600">Product ID:</span>
                        <span className="font-semibold text-gray-900">
                          #{item.id}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-600">Available Quantity:</span>
                        <span className={`font-semibold ${isOutOfStock ? 'text-red-600' : 'text-green-600'}`}>
                          {item.quantity} units
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Action Buttons */}
                <div className="space-y-3">
                  <Button
                    variant={isOutOfStock ? 'secondary' : 'primary'}
                    size="lg"
                    fullWidth
                    disabled={isOutOfStock || false}
                  >
                    {isOutOfStock ? 'Out of Stock' : 'Add to Cart'}
                  </Button>
                  <Button
                    variant="secondary"
                    size="lg"
                    fullWidth
                    onClick={() => navigate('/items')}
                  >
                    Continue Shopping
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
